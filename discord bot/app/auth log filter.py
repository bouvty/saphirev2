"""Keep request audit events while removing auth bearer keys and OAuth query values."""
import logging

class AuthURLFilter(logging.Filter):
    def filter(self,record):
        if isinstance(record.args,tuple) and len(record.args)==5 and isinstance(record.args[2],str):
            args=list(record.args)
            target=args[2]
            path=target.split('?',1)[0]
            for prefix in ('/auth/v2/discord/','/internal/auth/v2/discord/'):
                if path.startswith(prefix):
                    args[2]=prefix+'[redacted]'
                    break
            else:
                if path in ('/auth/callback','/auth/start') and '?' in target:
                    args[2]=path+'?[redacted]'
            record.args=tuple(args)
        return True

def install():
    logger=logging.getLogger('uvicorn.access')
    if not any(isinstance(item,AuthURLFilter) for item in logger.filters):
        logger.addFilter(AuthURLFilter())
