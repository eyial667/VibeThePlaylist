# Genre Specification Feature — Precise Subgenre Mapping

## Overview
Replace the app's current generic genre suggestions with more precise subgenres so recommendations and UI labels better reflect user intent (e.g., hip-hop/rap → Cloud Rap, Drill, Trap; Latin → Reggaeton, Salsa, Bachata).

## Goals
- Provide automatic subgenre determination based on user input, track characteristics, or contextual signals.
- Surface precise genres in the UI and playlist metadata.
- Use precise genres for playlist generation and recommendation logic.

## Acceptance Criteria
- The app automatically determines and applies appropriate subgenres without manual user selection.
- Existing playlists remain compatible; fallback to broad genre if no subgenre is determined.
- Automated tests cover subgenre determination and recommendation fallbacks.

## Example Mappings (Starter List — Expandable)
- hip-hop / rap: Cloud Rap, Drill, Trap, Boom Bap
- latin: Reggaeton, Salsa, Bachata, Latin Pop
- electronic: House, Techno, Drum & Bass, Ambient
- rock: Classic Rock, Alternative, Indie Rock, Punk
- pop: Synthpop, Indie Pop, Dance Pop

## Data/Model Changes
- Add a subgenre mapping configuration (implementation details TBD).
- Track determined_subgenre in playlist/request entities (nullable to preserve backwards compatibility).

## Backend Changes
- Implement automatic subgenre determination logic based on contextual signals (exact stack and approach TBD during implementation).
- Endpoint(s) to retrieve subgenre metadata or mappings.
- Update playlist-generation logic to accept and utilize determined subgenre; fallback to broad genre if unavailable.
- Migration: add new data model field (determined_subgenre) with null default; backfill if desired.

## Frontend Changes
- Display determined subgenres in playlist metadata and UI.
- Maintain graceful fallback if subgenre determination unavailable.
- Update API payloads to reflect resolved subgenres.

## API Changes
- Response: include determined_subgenre in playlist metadata.
- Internal: propagate subgenre info through recommendation pipeline.

## Testing
- Unit tests for subgenre determination logic and fallback behavior.
- E2E tests for playlist generation: assert resolved subgenre is applied and influences track selection.
- Tests for backwards compatibility with existing playlists without subgenres.

## Rollout Plan
- Feature-flag the precise-genre system for staged rollout.
- Telemetry: track subgenre determination success rates and coverage.

## Tasks (Initial)
1. Design and implement automatic subgenre determination mechanism.
2. Add subgenre mapping configuration.
3. DB migration to add determined_subgenre field.
4. Update playlist generation service to utilize determined subgenre.
5. Frontend: display determined subgenres and handle fallbacks.
6. Tests and feature flag.
7. Documentation update.

## Estimated Effort
3–5 dev days (adjust after finalizing determination strategy and subgenre coverage).

## Open Questions
- What signals should drive automatic subgenre determination (audio features, metadata analysis, seed tracks, etc.)?
- Where should the master subgenre list live (code config, DB, or external service)?
- Which broad genres need immediate coverage?
- Should admin tools be provided for subgenre mapping adjustments post-launch?

## Next Steps
- Finalize subgenre determination strategy.
- Begin implementation based on chosen approach.
