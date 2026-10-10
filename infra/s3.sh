#!/bin/sh
set -eu
export AWS_ACCESS_KEY_ID="$(cat /bootstrap/s3_admin_id)"
export AWS_SECRET_ACCESS_KEY="$(cat /bootstrap/s3_admin_secret)"
exec weed server -s3 -dir=/data -master.dir=/data -ip=s3 -ip.bind=0.0.0.0 -master.peers=none -master.volumeSizeLimitMB=1024 -volume.max=0 -master.telemetry=false -s3.iam.readOnly=false -s3.iam.config=/bootstrap/s3_iam.json -s3.port.iceberg=0 -s3.port.lance=0
