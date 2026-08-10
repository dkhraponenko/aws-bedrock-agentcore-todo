# Single-table layout: one partition per user, one item per todo entry.
# Sort keys are time-sortable ids, so a Query returns a user's list in
# creation order without a secondary index — and nothing here ever Scans.
resource "aws_dynamodb_table" "todo" {
  name         = "${var.project_name}-items"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  range_key    = "item_id"

  attribute {
    name = "user_id"
    type = "S"
  }

  attribute {
    name = "item_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }
}
