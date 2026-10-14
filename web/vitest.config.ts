import { defineConfig } from 'vitest/config';

// The tests here are the TypeScript half of the fixtures in `tests/golden`,
// which Python writes. They read those files off disk and do no rendering, so
// they run in node rather than in a simulated browser.
export default defineConfig({
  test: {
    globals: true,
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
