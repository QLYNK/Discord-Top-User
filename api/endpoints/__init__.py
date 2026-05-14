from .music_edit import register as register_music_edit
from .music_process import register as register_music_process
from .music_track_item import register as register_music_track_item
from .music_tracks import register as register_music_tracks
from .public_commands import register as register_public_commands
from .public_guild_detail import register as register_public_guild_detail
from .public_guilds import register as register_public_guilds
from .stats import register as register_stats
from .utilities_keyword_item import register as register_utilities_keyword_item
from .utilities_keywords import register as register_utilities_keywords
from .utilities_note_item import register as register_utilities_note_item
from .utilities_notes import register as register_utilities_notes
from .utilities_quiz import register as register_utilities_quiz
from .utilities_quiz_item import register as register_utilities_quiz_item
from .utilities_tad import register as register_utilities_tad
from .utilities_tad_item import register as register_utilities_tad_item


def register_api_endpoints(app, deps):
    register_stats(app, deps)
    register_public_commands(app, deps)
    register_public_guilds(app, deps)
    register_public_guild_detail(app, deps)
    register_music_tracks(app, deps)
    register_music_track_item(app, deps)
    register_music_edit(app, deps)
    register_music_process(app, deps)
    register_utilities_keywords(app, deps)
    register_utilities_keyword_item(app, deps)
    register_utilities_notes(app, deps)
    register_utilities_note_item(app, deps)
    register_utilities_tad(app, deps)
    register_utilities_tad_item(app, deps)
    register_utilities_quiz(app, deps)
    register_utilities_quiz_item(app, deps)
