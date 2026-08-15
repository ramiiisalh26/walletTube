from youtube_transcript_api import YouTubeTranscriptApi
try:
    YouTubeTranscriptApi.list_transcripts("ooYKMPlIlwQ", cookies="cookies.txt")
except Exception as e:
    print(type(e).__mro__)
    print(repr(e)[:400])