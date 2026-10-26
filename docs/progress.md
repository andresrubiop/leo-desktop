# Public distribution state

2026-10-26: prepared a curated public distribution from LEO 0.19.1 (`621a8c335c9e6ff2ff03ee04f3b46cffb8c2d8e0`). Runtime source is preserved; private photos, user state and the operational journal are excluded. Added a project-level MIT license, English onboarding, architecture/security/testing notes and CI.

Validation: source unit suite collected 195 tests; 194 passed, one optional OpenCV test skipped. Public distribution and CI checks are recorded in its commit/release history. No user extension was installed or enabled during preparation.

Pending integration limits: hardware-specific multi-monitor pointing, optional voice tools and model-specific tool selection. These are not marked as validated by unit-test results.
