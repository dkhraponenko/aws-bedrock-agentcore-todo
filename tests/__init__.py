"""The test suite, in layers.

Two independent questions decide where a test lives. The directory answers how
much of the system it covers; the `aws` marker answers whether it needs a
deployed stack, credentials and a synced .env. Only the combinations that mean
something exist:

    unit/               everything under the code being tested is substituted
    integration_tests/  several real components wired together - moto, a real
                        socket, the real lambda_handler - plus, marked `aws`,
                        the deployed gateway and the Lambda behind it
    e2e/                the whole path, from a chat turn to the table, model
                        included; always `aws`

`addopts` carries `-m 'not aws'`, so a default run is the offline half of that
table and needs no credentials at all. The directory then picks between the two
deployed halves: `pytest tests/integration_tests -m aws` is deterministic and
free, `pytest tests/e2e -m aws` calls the model and costs cents.
"""
