import { buildApp } from './app.js';
import { readConfig } from './config/env.js';

try {
  const config = readConfig();
  const app = await buildApp(config);
  let closing = false;
  for (const signal of ['SIGINT', 'SIGTERM'] as const) {
    process.on(signal, async () => {
      if (closing) return;
      closing = true;
      try { await app.close(); } catch { process.exitCode = 1; }
    });
  }
  try {
    await app.listen({ host: config.host, port: config.port });
  } catch (error) {
    await app.close();
    throw error;
  }
} catch (error) {
  console.error('Server startup failed. Check environment and database connectivity.', error);
  process.exitCode = 1;
}
