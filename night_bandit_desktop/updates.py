"""Anonymous public version lookup and manual instructions. Never downloads or runs an update."""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from night_bandit import __version__
from .update_source import PUBLIC_GITHUB_REPOSITORY,RELEASE_BRANCH

class UpdateError(Exception):pass


def version_key(text):
    if not isinstance(text,str) or len(text)>100:raise UpdateError('The release version format is unsupported.')
    match=re.fullmatch(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?',text)
    if not match:raise UpdateError('The release version format is unsupported.')
    pre=match[4];tokens=[]
    if pre:
        for token in pre.split('.'):
            if token.isdigit():
                if len(token)>1 and token.startswith('0'):raise UpdateError('The release version format is unsupported.')
                tokens.append((0,int(token)))
            else:tokens.append((1,token))
    return (*map(int,match.group(1,2,3)),1 if pre is None else 0,tuple(tokens))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise UpdateError('The update address redirected unexpectedly. Try again later.')


def public_json(url):
    # Only the application-owned public repository is requested. No vault/auth code is imported.
    request=urllib.request.Request(url,headers={'Accept':'application/vnd.github+json','User-Agent':'NIGHT-BANDIT/'+__version__,'X-GitHub-Api-Version':'2026-03-10'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=8) as response:raw=response.read(512*1024+1)
        if len(raw)>512*1024:raise UpdateError('The update response is too large. View the public repository instead.')
        data=json.loads(raw)
        if not isinstance(data,dict):raise ValueError()
        return data
    except urllib.error.HTTPError as error:
        if error.code==404:raise LookupError('not_found') from None
        if error.code in (403,429):raise UpdateError('GitHub is limiting requests. Please try again later.') from None
        raise UpdateError('GitHub could not complete the update check. Try again later.') from None
    except (urllib.error.URLError,TimeoutError,OSError):raise UpdateError('Cannot reach GitHub. Check your connection and try again.') from None
    except (ValueError,UnicodeError):raise UpdateError('GitHub returned an unreadable update response.') from None


@dataclass(frozen=True)
class UpdateResult:
    status:str
    current:str
    latest:str=''
    message:str=''
    release_url:str=''
    instructions:str=''


def manual_instructions(repo):
    if RELEASE_BRANCH!='main':raise UpdateError('The application update branch needs maintainer review.')
    return f'''1. Read the release notes. Stop Automatic Sync and Exit NIGHT BANDIT, including its tray, before updating.
2. Open a terminal in the ORIGINAL NIGHT BANDIT APPLICATION checkout. Do not use a repository or file that NIGHT BANDIT monitors.
3. Check the folder before continuing:

git remote get-url origin
git branch --show-current
git status --short

Continue only when origin is https://github.com/{repo}.git (without .git is also OK), branch is main, and git status --short prints nothing.
If anything differs, stop and review. Do not reset, discard, force-pull or delete your configuration.
4. Update the application source and bundled assets:

git pull --ff-only origin main

5. Review any release-specific prerequisites, then reopen the application:

pythonw -B run-night-bandit.pyw

6. Confirm the running version in Settings, reconnect/review your scope if needed, run a safe dry-run, then explicitly Start Automatic Sync.

Profiles and Windows-protected sign-in belong outside the application checkout. Updating never authorizes a new synchronization session. Python and Git are one-time prerequisites; this button runs none of these commands.'''


def check_updates(current=__version__,repo=PUBLIC_GITHUB_REPOSITORY,fetch=public_json):
    if not repo:return UpdateResult('not_configured',current,message='The public update repository has not been configured yet.')
    if not isinstance(repo,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo) or repo.casefold()!=PUBLIC_GITHUB_REPOSITORY.casefold():
        return UpdateResult('error',current,message='The application update source needs maintainer review.')
    try:
        current_key=version_key(current);base='https://api.github.com/repos/'+repo
        try:metadata=fetch(base)
        except LookupError:return UpdateResult('unavailable',current,message='The update repository was not found or is not public.')
        if metadata.get('private') is not False or str(metadata.get('full_name','')).casefold()!=repo.casefold():
            raise UpdateError('The update repository identity could not be verified as public.')
        if metadata.get('default_branch')!=RELEASE_BRANCH:raise UpdateError('The update branch changed. Maintainer review is required.')
        try:release=fetch(base+'/releases/latest')
        except LookupError:return UpdateResult('no_release',current,message='No published release yet. Your installed version has not been compared with a release.')
        if release.get('draft') is not False or release.get('prerelease') is not False:raise UpdateError('No supported stable release was returned.')
        tag=release.get('tag_name');latest_key=version_key(tag)
        if latest_key[3]!=1:raise UpdateError('The latest release is not a stable version.')
        url='https://github.com/'+repo+'/releases/tag/'+urllib.parse.quote(tag,safe='')
        if latest_key>current_key:return UpdateResult('available',current,tag.lstrip('v'),'A newer version is available.',url,manual_instructions(repo))
        if latest_key==current_key:return UpdateResult('current',current,tag.lstrip('v'),'You are running the latest published version.',url)
        return UpdateResult('ahead',current,tag.lstrip('v'),'This build is newer than the latest published release.',url)
    except UpdateError as error:return UpdateResult('error',current,message=str(error))
    except (ValueError,TypeError,AttributeError):return UpdateResult('error',current,message='The update response could not be verified.')
