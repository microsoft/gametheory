import ReactDOM from 'react-dom/client';
import { App } from './App';
import { LabClient } from './api';
import { EntraAuthentication } from './auth';
import { configuration } from './config';
import './styles.css';

const config = configuration();
const auth = config.ready ? new EntraAuthentication(config.value) : null;
const operations = config.ready && auth ? new LabClient(config.value.apiBaseUrl, auth) : null;

ReactDOM.createRoot(document.getElementById('root')!).render(
  <App auth={auth} operations={operations} />,
);
