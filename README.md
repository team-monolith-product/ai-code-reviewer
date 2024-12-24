# AI Code Reviwer

This GitHub Action automatically performs a code review for incoming Pull Requests using the ChatGPT (O1) model via the OpenAI API. It reads your pre-defined coding rules, examines diffs of changed files, generates inline comments, and posts them on GitHub PRs. If there are no suggestions, it automatically approves the Pull Request.

---

## How It Works

1. **Pull Request Info**  
   When a Pull Request (PR) event triggers this Action, it uses PyGithub to:
   - Retrieve the PR details (title, body, diffs).
   - Check whether the current bot user is requested for a review (or if a recent review has already been made).

2. **Diff Parsing**  
   It collects the patch (diff) of each modified file in the PR and processes it using the [unidiff](https://pypi.org/project/unidiff/) library.

3. **Coding Rules**  
   The script looks for a `.github/coding-rules.md` file (in your repo) that contains your project's or organization's coding guidelines.

4. **Sending to ChatGPT**  
   Using the OpenAI API (ChatGPT/O1 model):
   - It constructs a prompt that includes:
     - The coding rules
     - The PR body
     - The diff contents
   - The model responds with JSON-formatted comments (inline suggestions, warnings, etc.).

5. **Posting Review Comments**  
   The returned comments are posted back to the PR using [PyGithub](https://pygithub.readthedocs.io/).  
   If there are no AI suggestions, it automatically approves the PR.

---

## Usage

1. **Create a GitHub Workflow**  
   In your repository, create a YAML workflow file (e.g., `.github/workflows/ai-code-reviewer.yml`) with the following example content:
   ```yaml
    name: AI Code Reviewer

    on:
        pull_request:
            types: [opened, synchronize, reopened]

    permissions:
        contents: write
        pull-requests: write

    jobs:
        ai-code-reviewer:
            name: ai-code-reviewer
            runs-on: ubuntu-latest
            steps:
            - uses: actions/checkout@v4
            - name: Run ChatGPT Code Review
                uses: team-monolith-product/ai-code-reviewer@main
                with:
                GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
                OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
                PR_NUMBER: ${{ github.event.number }}
                SYSTEM_PROMPT: Always answer in Korean.
   ```

2. **Add Your Coding Rules**  
   - Create a file in your repo at `.github/coding-rules.md`.  
   - Write down your coding standards, best practices, etc.

3. **Secrets and Environment**  
   - `GITHUB_TOKEN`: Any GitHub token with appropriate permissions. `GITHUB_TOKEN` is automatically provided by GitHub Actions. If you have a bot account, you can use its token. It is usually better, because you can re-request the review from the bot account.
   - `OPENAI_API_KEY`: Your OpenAI API key (stored as a secret in your repo).  


**Enjoy automated code reviews with ChatGPT!**