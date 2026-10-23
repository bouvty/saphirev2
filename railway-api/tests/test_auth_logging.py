import logging
from app.auth_log_filter import AuthURLFilter

def event(target):
    return logging.LogRecord('uvicorn.access',20,'',0,'%s - "%s %s HTTP/%s" %s',('client','GET',target,'1.1',200),None)

def test_auth_secrets_are_redacted_but_request_audit_is_retained():
    for target in ['/auth/v2/discord/'+'a'*43,'/auth/callback?code=secret&state=secret','/internal/auth/v2/discord/success/secret']:
        record=event(target)
        assert AuthURLFilter().filter(record)
        message=record.getMessage()
        assert 'secret' not in message and 'a'*43 not in message
        assert 'GET' in message and '200' in message and '[redacted]' in message

def test_ordinary_requests_keep_their_audit_paths():
    record=event('/health')
    AuthURLFilter().filter(record)
    assert '/health' in record.getMessage()
