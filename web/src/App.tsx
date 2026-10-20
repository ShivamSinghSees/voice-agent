import React from 'react';
import { Routes, Route, NavLink } from 'react-router-dom';
import { CustomerCallPage } from './pages/CustomerCallPage';
import { OperatorPage } from './pages/OperatorPage';

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
            Operator View
          </NavLink>
        </nav>
      </header>

      <main>
        <Routes>
          <Route path="/" element={<CustomerCallPage />} />
          <Route path="/operator" element={<OperatorPage />} />
        </Routes>
      </main>
    </div>
  );
};
