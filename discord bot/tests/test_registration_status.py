"""In-process tests: simulate verified DB rows and Discord edit methods."""
import ast, asyncio, pathlib, types, unittest
from unittest.mock import AsyncMock

SRC=pathlib.Path(__file__).resolve().parents[1] / 'app' / 'bot.py'

def monitor_fn():
    tree=ast.parse(SRC.read_text())
    node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='monitor_registration')
    module=ast.Module(body=[node],type_ignores=[])
    ns={'asyncio':asyncio,'REGISTRATION_CHECK_INTERVAL':0,'REGISTRATION_TIMEOUT':600,'time':types.SimpleNamespace(monotonic=lambda:0), 'log':types.SimpleNamespace(exception=lambda *a,**kw:None,warning=lambda *a,**kw:None)}
    class Discord:
        NotFound=type('NotFound',(Exception,),{})
        HTTPException=type('HTTPException',(Exception,),{})
    ns['discord']=Discord
    exec(compile(module,'monitor_registration','exec'),ns)
    return ns

class TestRegistrationStatus(unittest.IsolatedAsyncioTestCase):
    async def test_success_edits_original_embed(self):
        ns=monitor_fn()
        calls=0
        async def verified(user_id):
            nonlocal calls
            calls+=1
            return calls>=2
        ns['is_verified']=verified
        ns['registration_success_embed']=lambda u:'success'
        ns['emb']=lambda *a,**kw: 'pending'
        ns['cv2_layout']=lambda value,controls=None:value
        user=types.SimpleNamespace(id=123)
        class View:
            verified_displayed=False
            ended=False
            def is_finished(self):return self.ended
            def stop(self):self.ended=True
        view=View();msg=types.SimpleNamespace(edit=AsyncMock())
        await ns['monitor_registration'](msg,user,view)
        msg.edit.assert_awaited_once_with(view='success')
        self.assertTrue(view.verified_displayed)
        self.assertGreaterEqual(calls,2)

    async def test_manual_confirmation_does_not_overwrite_success(self):
        ns=monitor_fn();ns['is_verified']=AsyncMock(return_value=False)
        ns['registration_success_embed']=lambda u:'success'
        ns['emb']=lambda *a,**kw:'timeout'
        ns['cv2_layout']=lambda value,controls=None:value
        user=types.SimpleNamespace(id=123)
        class View:
            verified_displayed=True
            def is_finished(self):return True
            def stop(self):pass
        msg=types.SimpleNamespace(edit=AsyncMock())
        await ns['monitor_registration'](msg,user,View())
        msg.edit.assert_not_awaited()

if __name__=='__main__':unittest.main()
