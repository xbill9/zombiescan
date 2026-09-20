# zombiescan

Find the AWS resources nobody is using, and what they cost you.

Not "here are your 14 EBS volumes" — "9 of these are attached to nothing and
cost you $47/month, here is the plan to kill them."

## Local-first

zombiescan runs on your machine with your existing AWS credentials. There is no
hosted service, no cross-account IAM role to create, and no data sent anywhere.

It is **read-only**: Describe/List/Get calls only. It never deletes anything. The
cleanup commands it generates are written to a file for you to read and run
yourself.

## Install

```
uv sync
uv run zombiescan scan
```

## Usage

```
uv run zombiescan scan                      # default region
uv run zombiescan scan --all-regions        # every enabled region
uv run zombiescan scan --json findings.json # machine-readable output
uv run zombiescan scan --script cleanup.sh  # write the (unexecuted) cleanup plan
```

MIT.
