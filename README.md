# Lia Content Agent

**v0.1 · Local, human-in-the-loop multimodal video keyframe workflow**

Lia Content Agent is a personal software engineering project that turns a repeatable video keyframe-selection process into a working Python application. It analyzes a reference video with Qwen, extracts candidate frames with FFmpeg, and lets a human review, request another recommendation round, and approve the final frame.

The project's primary purpose is to **implement a real workflow as a tested software system**. It is not a commercial product or a general-purpose autonomous AI agent.

## What is implemented

- **New task creation:** validate a local task directory; save the reference video and structured task metadata.
- **Multimodal recommendation:** upload the video to Alibaba Cloud Model Studio (DashScope) once per in-memory recommendation session; ask Qwen for up to three candidate timestamps and structured reasons.
- **Deterministic extraction:** validate model output, map timestamps to decoded video frames, and extract PNG images using FFmpeg.
- **Human review and feedback:** review each round, select a frame, or submit feedback for another Qwen recommendation while preserving the conversation history in memory.
- **Local task records:** keep round-specific model replies, candidate metadata, selection records, and task state in JSON/text files.
- **Recovery of saved results:** open a past task from its absolute path, browse its saved candidate rounds, and select a saved frame without calling Qwen again.
- **Controlled final replacement:** after the first approval, allow one final replacement; back up the original frame to `1f-original-bk.png`, preserve selection history, and lock subsequent replacements. Legacy `1f-original.png.bk` backups are recognized.
- **Offline tests:** unit tests with mocked Qwen responses, plus integration tests using local FFmpeg when available.

## Scope and limitations

- **Supported API:** Alibaba Cloud Model Studio **Qwen / DashScope** only. A compatible Beijing-region endpoint/workspace is expected by the current API client.
- **Implemented creative workflow:** Seedance 2.5 multi-shot / single-keyframe reshape, specifically its **candidate-frame recommendation and review** stage. Other scene entries are disabled or reserved; Story/Prompt generation is **not implemented** in v0.1.
- **Not an autonomous agent:** the Python workflow determines when Qwen, FFmpeg, and storage operations run. The model recommends frames but does not autonomously plan and invoke arbitrary local tools.
- **Recovery is file-based:** saved candidates and approvals can be reopened, but the app does **not** resume the original Qwen conversation across process restarts.
- **Single-user and local UI:** no database, user accounts, cloud hosting, or production SLA.
- **Privacy:** video analysis **uploads the selected video to Alibaba Cloud** when a Qwen recommendation is requested. The interface and workflow records are otherwise local.
- Quality depends on the configured Qwen model; recommendations are not guaranteed to match an expert creator's judgment.

## Architecture

```text
Streamlit UI (app.py, lia_agent/ui/)
         |
         v
Workflow (lia_agent/workflows/keyframe_workflow.py)
     /           |                \
    v            v                 v
Qwen API      FFmpeg          Storage (JSON/files)
Service       Service        task + candidate records
    \            |                 /
       Human review / approval
```

Main responsibilities:

| Layer | Responsibilities |
| --- | --- |
| UI | Streamlit pages, user inputs, selection and confirmation dialogs |
| Workflow | Qwen message history, execution sequence, approval/replacement rules |
| Services | Provider-specific Qwen API and precise FFmpeg frame extraction |
| Storage | Local task creation, round history, JSON state and selection records |
| Tests | Unit, mock integration and optional real-FFmpeg verification |

## Requirements

- macOS (the project's primary development/test environment)
- Python **3.11** recommended
- **FFmpeg and FFprobe** installed and available in `PATH`
- An Alibaba Cloud Model Studio (DashScope) API Key, an accessible Qwen model, and a supported Base URL

`requirements.txt` lists **Python packages only**. FFmpeg / FFprobe are external command-line tools and must be installed separately.

## Installation

Run the commands from the repository root:

```bash
# Create a Conda environment (you may choose a different environment name)
conda create -n lia_content_agent python=3.11 -y
conda activate lia_content_agent

# Install Python dependencies
python -m pip install -r requirements.txt

# Install FFmpeg and FFprobe using Conda
conda install -c conda-forge ffmpeg
# Alternatively, on macOS with Homebrew: brew install ffmpeg

# Verify that the external tools are available
ffmpeg -version
ffprobe -version
```

### Configure Qwen

Copy the sample configuration **inside the package**:

```bash
cp lia_agent/config-example.py lia_agent/config.py
```

To obtain your credentials, follow the official [Alibaba Cloud Model Studio API Key guide](https://help.aliyun.com/zh/model-studio/get-api-key). In the Model Studio console, check the **API Host** for the Beijing-region workspace you intend to use; configure `DASHSCOPE_BASE_URL` with the corresponding HTTPS endpoint ending in `/api/v1`. Use an API Key with access to that workspace. Do not copy another user's workspace endpoint or credentials.

Open `lia_agent/config.py` and set:

- `DASHSCOPE_BASE_URL`: your DashScope **HTTPS** Base URL ending with `/api/v1`, using the Beijing-region endpoint/workspace supported by the client.
- `DASHSCOPE_API_KEY`: your **own** DashScope API Key. Never commit this file with real credentials.
- `KEYFRAME_RULES`: optional selection criteria for candidate source frames.
- `KEYFRAME_OUTPUT_RULES`: optional response-format instructions. Keep the required JSON field names and types, otherwise the parser may reject the response.

`QWEN_MODEL` and `VIDEO_FPS` have defaults in the sample. Ensure the selected Qwen model is available to your account.

**Security:** `lia_agent/config.py` is intentionally ignored by Git. Only `lia_agent/config-example.py` should be committed. If you previously tracked a real-key config, remove it from the Git index/history and revoke that exposed Key before publishing.

### Run the app

```bash
python -m streamlit run app.py
```

Open the local URL shown by Streamlit (typically `http://localhost:8501`). Select **Seedance 2.5** and the enabled single-keyframe scene, or choose the saved-task recovery entry.

### Run tests

```bash
python -m unittest discover -s tests -v
```

The test suite does **not** make live Qwen API calls. Some tests use real local FFmpeg/FFprobe; those require the executables to be present. With the provided sources, **67 tests** passed in an isolated offline validation environment.

## Example task output

A completed task keeps its records outside the source repository:

```text
/path/to/a/production-task/
├── reference-video.mp4      # may also be .mov or .m4v
├── 1f-original.png          # currently approved candidate
├── 1f-original-bk.png       # only if final replacement was used
└── workspace/
    ├── task.json
    └── candidates_recomm/
        ├── inputs.json
        ├── state.json
        ├── selection.json    # after an approval
        ├── round_01/
        │   ├── reply.txt
        │   ├── candidates.json
        │   └── candidate_*.png
        └── round_02/        # optional subsequent recommendation
            └── ...
```

Actual REELS videos, images, and production directories are **not** included in this repository.

## Known design choices

- Explicit Python workflow orchestration rather than LangChain or LangGraph; the current bounded task benefits from simpler deterministic steps.
- Human approval is required for the final creative decision.
- Local JSON/file persistence keeps the project lightweight, but multi-file updates are not crash-proof database transactions.
- This release emphasizes working code, test coverage, and clear module boundaries rather than claimed business impact.

## License

No license has been selected for this repository. Public visibility alone does not grant reuse rights. Add a license later if you want to permit third-party redistribution or modification.
