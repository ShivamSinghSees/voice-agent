import React from 'react';
import { Routes, Route, NavLink } from 'react-router-dom';
import { CustomerCallPage } from './pages/CustomerCallPage';
import { OperatorPlaceholderPage } from './pages/OperatorPlaceholderPage';

export const App: React.FC = () => {
  return (
    <div className="app-container">
      <header>
        <h1>Voice AI Agent</h1>
        <nav>
          <NavLink to="/" className={({ isActive }) => (isActive ? 'active' : '')}>
            Customer Call
          </NavLink>
          <NavLink to="/operator" className={({ isActive }) => (isActive ? 'active' : '')}>
            Operator View (Phase 5)
          </NavLink>
        </nav>
      </header>

      <main>
        <Routes>
          <Route path="/" element={<CustomerCallPage />} />
          <Route path="/operator" element={<OperatorPlaceholderPage />} />
        </Routes>
      </main>
    </div>
  );
};
