import { describe, it, expect } from 'vitest';
import { VERSION } from './index.js';

describe('@sentihome/shared', () => {
  it('exposes a version', () => {
    expect(VERSION).toBe('0.1.0');
  });
});
