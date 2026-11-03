"""Negative authorization and incomplete role output acceptance regressions."""
import json
from unittest.mock import patch
from .helpers import BASE,read_chapter,observe,operation,submit
from .test_reader import analyzed_session,prepare_generation
from app.providers import MockAdapter

def test_F19_F23_partial_role_turn_never_enters_conversation_memory(client,reader,demo,profile):
    created,_,_=analyzed_session(client,reader,demo,profile)
    session=created.json()
    original=MockAdapter.generate
    def incomplete(self,p,prompt,data,limit):
        result=original(self,p,prompt,data,limit)
        if prompt.startswith('role_chat-'):result['finishReason']='length'
        return result
    payload={'operation':'role_chat','profileId':profile['id'],'sessionId':session['id'],'message':'此刻你在想什么？','turnId':'partial-turn'}
    with patch.object(MockAdapter,'generate',incomplete):task,_,_=submit(client,profile,payload)
    assert task['resultOutcome']=='partial' and not task['result']['replySaved'],task
    assert client.get(BASE+f"/role-sessions/{session['id']}/messages").json()['items']==[]
    complete,_,_=submit(client,profile,{**payload,'turnId':'new-authorized-turn'})
    assert complete['state']=='succeeded' and complete['result']['replySaved']
    messages=client.get(BASE+f"/role-sessions/{session['id']}/messages").json()['items']
    assert len(messages)==2 and all(m['turnId']=='new-authorized-turn' for m in messages)

def test_F25_other_owner_cannot_read_or_mutate_role_profile_progress_bookmark_or_consent(client,reader,demo,profile):
    created,_,anchor=analyzed_session(client,reader,demo,profile)
    sid=created.json()['id']
    marked=client.post(BASE+f"/books/{demo['bookId']}/bookmarks",json={'anchor':anchor,'note':'private'},headers={'Idempotency-Key':'private-bookmark'}).json()
    payload={'operation':'role_chat','profileId':profile['id'],'sessionId':sid,'message':'你好','turnId':'private-turn'}
    planned=client.post(BASE+'/operation-plans',json=payload).json()
    consent=client.post(BASE+'/consents',json={'planId':planned['planId'],'planHash':planned['planHash'],'profileId':profile['id'],'maxCalls':planned['maxCalls'],'inputTokens':planned['inputTokens'],'outputTokens':planned['outputTokens']}).json()
    client.headers.pop('Authorization')
    assert client.post(BASE+'/auth/register',json={'username':'other_private','password':'private-negative-123'}).status_code==201  # gitleaks:allow -- synthetic test fixture
    other=client.post(BASE+'/auth/login',json={'username':'other_private','password':'private-negative-123','clientType':'android'}).json()  # gitleaks:allow -- synthetic test fixture
    client.headers['Authorization']='Bearer '+other['accessToken']
    scope=f"branchId={demo['branchId']}&revisionId={demo['revisionId']}"
    read_paths=[f'/role-sessions/{sid}',f'/role-sessions/{sid}/messages',f"/books/{demo['bookId']}/progress?{scope}",f"/books/{demo['bookId']}/bookmarks?{scope}",f"/books/{demo['bookId']}/search?query=林渡&{scope}",f"/books/{demo['bookId']}/branches"]
    for path in read_paths:
        response=client.get(BASE+path)
        assert response.status_code==404,(path,response.text)
        assert all(value not in response.text for value in ['林渡','TEST-KEY','private-turn'])
    mutations=[('PATCH',f"/model-profiles/{profile['id']}",{'expectedVersion':1,'name':'stolen'}),('DELETE',f"/books/{demo['bookId']}/bookmarks/{marked['id']}",None),('POST',f'/role-sessions/{sid}/turns',{**payload,'consentId':consent['consentId']}),('POST','/consents',{'planId':planned['planId'],'planHash':planned['planHash'],'profileId':profile['id'],'maxCalls':planned['maxCalls'],'inputTokens':planned['inputTokens'],'outputTokens':planned['outputTokens']})]
    for method,path,body in mutations:
        response=client.request(method,BASE+path,json=body,headers={'Idempotency-Key':'other-owner-'+method})
        assert response.status_code==404,(path,response.text)
