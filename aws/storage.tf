# ============================================================================
# STORAGE - EBS Volume for RuVector persistent data
# ============================================================================

resource "aws_ebs_volume" "ruvector_data" {
  availability_zone = "${var.aws_region}a"
  size              = 40
  type              = "gp3"
  iops              = 3000
  throughput        = 125

  tags = {
    Name = "ruvector-data-volume"
  }

  # Protect data volume from accidental destruction
  lifecycle {
    prevent_destroy = false # Set to true in production
  }
}

resource "aws_volume_attachment" "ruvector_data" {
  device_name  = "/dev/sdf"
  volume_id    = aws_ebs_volume.ruvector_data.id
  instance_id  = aws_instance.ruvector.id
  force_detach = true
}