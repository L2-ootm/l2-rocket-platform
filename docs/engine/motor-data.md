# Motor data

Motor curves live in `l2_engine/motors/*.eng` and originate from OpenRocket's
bundled motor database. `ast_eval` scans the complete directory dynamically.

The invariant is:

`AST motor_designation == .eng header designation == OpenRocket designation`

Do not add Rust-side aliases. Refresh the pool from the local OpenRocket
database with the repository extraction script, then run the motor parser and
AST bridge tests.

Motor fitment includes the configured clearance and fails closed before
simulation. Propellant and burn behavior come from the curve data, not motor
class estimates or names.

