import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { App } from './App';
import { CallGuardProvider } from './context/CallGuardContext';
import './index.css';

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <BrowserRouter>
      <CallGuardProvider>
        <App />
      </CallGuardProvider>
    </BrowserRouter>
  </React.StrictMode>
);
