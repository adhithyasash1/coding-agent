# Local experiment rehearsal

`experiment.local.json` is a process-only rehearsal configuration. Copy it to a
separate run directory, replace the task and image pins, and update `status.json`
and the billing `attested_at` value from the trusted local provider and billing
attestation source before running:

```text
python3 scripts/run_experiment.py preflight --config examples/experiment.local.json --output /tmp/experiment-run
python3 scripts/run_experiment.py launch --config examples/experiment.local.json --output /tmp/experiment-run
```

The checked-in status and billing values are only fixtures for local testing.
They do not authenticate a cloud provider and this runner has no cloud launch
adapter.
