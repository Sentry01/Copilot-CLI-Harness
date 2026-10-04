/**
 * Acceptance support kit (managed by copilot-harness; frozen with the suite).
 * Specs import everything from here:  import { test, expect, ... } from '../../support';
 */
export { test, expect, uniqueId, TestData } from './fixtures';
export { appBaseURL, contract, appContract } from './contract';
export * from './perf';
export * from './a11y';
export * from './security';
