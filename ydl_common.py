"""yt-dlp 공통 옵션.

YouTube 는 일부 영상에서 visionOS/TV 플레이어 클라이언트 응답을 UNPLAYABLE 로 막는다
(아동용으로 표시된 영상 등). 그러면 web 클라이언트만 남는데, web 은 n 챌린지
(스트림 URL 서명 난독화) 해결을 요구한다. 챌린지 해결 스크립트 내려받기를 허용하지
않으면 쓸 수 있는 포맷이 하나도 남지 않아 "This video is not available" 로 실패한다.

스크립트는 처음 한 번만 GitHub 에서 받아 ~/.cache/yt-dlp 에 캐시하며, deno / node /
bun / quickjs 중 하나가 설치돼 있어야 동작한다. 런타임이나 네트워크가 없으면 yt-dlp 가
경고만 남기고 이 설정이 없을 때와 똑같이 동작하므로, 켜 두어서 잃는 것은 없다.
"""

# yt-dlp CLI 의 --remote-components ejs:github 과 같다.
REMOTE_COMPONENTS = ["ejs:github"]


def ydl_opts(opts: dict | None = None, **extra) -> dict:
    """호출부 옵션에 공통 옵션을 더한 새 dict 를 돌려준다(원본은 건드리지 않는다)."""
    return {"remote_components": list(REMOTE_COMPONENTS), **(opts or {}), **extra}
