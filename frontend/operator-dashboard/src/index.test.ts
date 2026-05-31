import { describe, it, expect } from 'vitest';
import { VERSION, SHARED_LIB_VERSION } from './index.js';

describe('@kukiihome/operator-dashboard', () => {
  it('exposes a version', () => {
    expect(VERSION).toBe('0.1.0');
  });

  it('imports from @kukiihome/shared workspace package', () => {
    expect(SHARED_LIB_VERSION).toBe('0.1.0');
  });
});
