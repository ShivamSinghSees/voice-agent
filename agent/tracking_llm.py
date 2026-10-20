"""Wrapper around LLM provider to track full generated text per turn.

Enables explicit inspection and logging of full generated text versus
the truncated text retained in chat history when a barge-in (interruption) occurs.
"""

from typing import Any
from livekit.agents import llm, types


class TrackingLLM(llm.LLM):
    """Wraps any LiveKit LLM to track the full generated response text across streaming chunks."""

    def __init__(self, inner: llm.LLM) -> None:
        super().__init__()
        self.inner = inner
        self.latest_full_text: str = ""

    @property
    def model(self) -> str:
        return getattr(self.inner, "model", "unknown")

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: types.APIConnectOptions = types.DEFAULT_API_CONNECT_OPTIONS,
        **kwargs: Any,
    ) -> llm.LLMStream:
        """Forward chat invocation to inner LLM while capturing stream deltas in latest_full_text."""
        # Reset turn tracking buffer
        self.latest_full_text = ""
        parent = self

        inner_stream = self.inner.chat(
            chat_ctx=chat_ctx,
            tools=tools,
            conn_options=conn_options,
            **kwargs,
        )

        class TrackingStream(llm.LLMStream):
            def __init__(self) -> None:
                super().__init__(
                    parent,
                    chat_ctx=chat_ctx,
                    tools=tools or [],
                    conn_options=conn_options,
                )

            async def _run(self) -> None:
                async for chunk in inner_stream:
                    if chunk.delta and chunk.delta.content:
                        parent.latest_full_text += chunk.delta.content
                    self._event_ch.send_nowait(chunk)

            async def aclose(self) -> None:
                await inner_stream.aclose()
                await super().aclose()

        return TrackingStream()
