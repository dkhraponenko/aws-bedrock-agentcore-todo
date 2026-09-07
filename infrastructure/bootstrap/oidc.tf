##############################################################################
# GitHub as an identity provider
#
# There is one of these per AWS account, shared by every repository that ever
# assumes a role from Actions. If the account already has one, import it rather
# than creating a second:
#
#   terraform import aws_iam_openid_connect_provider.github \
#     arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com
##############################################################################

resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

  # The audience a workflow asks STS for. configure-aws-credentials sends
  # exactly this value, and the trust policy checks it again on the way in.
  client_id_list = ["sts.amazonaws.com"]

  # thumbprint_list is deliberately absent. AWS has validated this endpoint
  # against its own trust store since 2023, and a thumbprint written down here
  # is a fingerprint that expires without telling anyone.
}
