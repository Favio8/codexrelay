import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import NotificationProvider from './Notifications';
import './style.css';
import './compact-layout.css';
import './settings-interactions.css';
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><NotificationProvider><App/></NotificationProvider></React.StrictMode>);
