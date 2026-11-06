import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readConfig } from '../src/config/env.js';

const production = {
  NODE_ENV: 'production', PGHOST: 'aws-0-ap-southeast-1.pooler.supabase.com',
  PGPORT: '5432', PGDATABASE: 'postgres', PGEXPECTED_DATABASE: 'postgres',
  PGUSER: 'sif_backend.example', PGPASSWORD: 'test-only-password',
  PGSSLMODE: 'verify-full', ADMIN_API_KEY: 'test-only-admin-key', PORT: '10000',
};

test('Render binds publicly on its supplied port and uses the bundled verified CA', () => {
  const config = readConfig(production);
  assert.equal(config.host, '0.0.0.0');
  assert.equal(config.port, 10000);
  assert.equal(config.database.user, 'sif_backend.example');
  assert.notEqual(config.database.ssl, false);
  if (config.database.ssl) {
    assert.equal(config.database.ssl.rejectUnauthorized, true);
    assert.match(config.database.ssl.ca ?? '', /BEGIN CERTIFICATE/);
  }
});

test('production rejects disabled TLS and an unprotected admin endpoint', () => {
  assert.throws(() => readConfig({ ...production, PGSSLMODE: 'disable' }), /verify-full/);
  assert.throws(() => readConfig({ ...production, ADMIN_API_KEY: '' }), /ADMIN_API_KEY/);
  assert.throws(() => readConfig({ ...production, PGEXPECTED_DATABASE: 'VB-SIF' }), /PGEXPECTED_DATABASE/);
});

test('development retains loopback binding and supports explicit certificate paths', () => {
  const config = readConfig({ ...production, NODE_ENV: 'development', ADMIN_API_KEY: '',
    PGSSLMODE: 'disable', PORT: '3000' });
  assert.equal(config.host, '127.0.0.1');
  assert.equal(config.port, 3000);
  assert.equal(config.database.ssl, false);
  assert.throws(() => readConfig({ ...production, PGSSLROOTCERT: 'missing-certificate.pem' }), /ENOENT/);
});
