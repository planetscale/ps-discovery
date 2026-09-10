"""
Query workload capture for PlanetScale Neki sharding design.

Opt-in and driven by `ps-discovery workload`. Nothing here runs during a normal
discovery run, and nothing here writes into the discovery output.

The job is to capture data and hand it to the planner in the format it expects.
No shard key is recommended, no topology proposed, no statement classified --
those need a candidate topology and a cost model, which the planner has and this
tool does not.
"""
