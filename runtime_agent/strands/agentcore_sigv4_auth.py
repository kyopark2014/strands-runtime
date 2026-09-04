import httpx2
import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


class AgentCoreSigV4Auth(httpx2.Auth):
    def __init__(self, region: str, service: str = "bedrock-agentcore"):
        self.region = region
        self.service = service

    def auth_flow(self, request: httpx2.Request):
        try:
            session_credentials = boto3.Session().get_credentials()
            if session_credentials is None:
                raise RuntimeError("AWS credentials are not available")
            credentials = session_credentials.get_frozen_credentials()
        except Exception as exc:
            raise RuntimeError(
                "Failed to resolve AWS credentials for SigV4 signing"
            ) from exc
        headers = dict(request.headers)
        body = request.content

        aws_request = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=body,
            headers=headers,
        )
        SigV4Auth(credentials, self.service, self.region).add_auth(aws_request)
        prepared = aws_request.prepare()

        for key, value in prepared.headers.items():
            request.headers[key] = value

        yield request
