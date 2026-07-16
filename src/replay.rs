// Keep the short `replay` alias behavior-identical while giving Cargo a
// distinct target path. Pointing two binaries at main.rs can leave a stale
// top-level executable after test builds.
include!("main.rs");
