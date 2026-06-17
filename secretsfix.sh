Prod rollout P9: rewrite promote step to docker buildx imagetools

  gcloud artifacts docker images copy does not exist in the SDK. Use
  gcloud auth configure-docker + docker buildx imagetools create to copy each
  image manifest test AR -> prod AR. imagetools wraps the prod tag in a new OCI
  index, so re-describe the original digest in the prod AR to confirm it landed
  and deploy by that original digest, not the tag.

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"