# Vendored Overcooked runtime

Source: the local `overcooked_ai/src/overcooked_ai_py` runtime used by the original project.
License: [LICENSE.overcooked](LICENSE.overcooked), MIT.

Included: native MDP/action/layout modules, utilities, rendering, and original graphics/fonts/layouts.
Excluded: training pipelines, agent benchmarks, web demo, human datasets and historical results.

The package initializer only declares the bundled runtime. Gym registration is not needed by this application.
`bootstrap.py` selects this runtime when running `run.py`, so an editable installation of the old repository is unnecessary.
