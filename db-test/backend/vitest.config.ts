import { defineConfig } from 'vitest/config';

export default defineConfig({ test: {
  environment: 'node', include: ['tests/scheme-performance*.spec.ts'],
  testTimeout: 30000,
} });
