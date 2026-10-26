# Architecture

The GNOME Shell extension (`extension/`) handles avatar movement, the conversation balloon and desktop integration. GTK4 provides secondary settings (`leo/ui.py`). A Python service communicates over D-Bus and keeps model/network work away from the shell process.

The agent (`leo/agent.py`) requests structured calls from a local Ollama model. The broker (`leo/broker.py`) validates arguments against a fixed schema, checks capabilities and binds approvals to one client. Mutating tools do not run merely because a model proposed them. Cancellation, expiry and revocation are tested.

File actions resolve paths within authorized roots, reject escapes through symlinks and avoid overwriting existing files. The undo journal records supported mutations. Local memory and reminders are separate from model conversation history.

Screen access and accessibility are optional. Attentive mode produces observations rather than free execution of tools. Model quality and precision depend on local installation and hardware.

Optional adapters include local voice tools and Cerveau document indexing. Cerveau paths are configurable; absence is reported rather than replaced with fabricated results. Account-usage and mail integrations are explicit features and do not permit remote inference.

Unit tests validate contracts and policy logic. Isolated GNOME/D-Bus tests cover different boundaries. Neither substitutes for validation on the real hardware and desktop version where LEO will run.
