// TEST ONLY entry point. Vite's production input is index.html, never this file.
import ReactDOM from 'react-dom/client';
import { App } from '../src/App';
import '../src/styles.css';
import { TestOnlyOperations, testAuth } from './fixtures';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <App auth={testAuth} operations={new TestOnlyOperations()} />,
);
