"""
Claude Code SDK 기반 AI 코드 리뷰 구현
"""

import asyncio

from github.PullRequest import PullRequest
from claude_code_sdk import ClaudeSDKClient, ClaudeCodeOptions


def review(
    pr: PullRequest,
    git_dir: str,
    system_prompt: str,
):
    """
    Perform AI-based code review using Claude Code SDK on the given PullRequest.
    Claude will post comments directly using gh CLI tools.
    
    Args:
        pr (PullRequest): The PyGithub PullRequest object to review.
        git_dir (str): The local path to the git repository.
        system_prompt (str): The system prompt to guide the AI model.
    """
    asyncio.run(get_claude_review(pr, git_dir, system_prompt))
    print("[INFO] Claude Code review process completed.")


async def get_claude_review(
    pr: PullRequest, 
    git_dir: str, 
    system_prompt: str
) -> None:
    """
    Send PR info to Claude Code SDK. Claude will post review comments directly using gh CLI.
    
    Args:
        pr (PullRequest): The pull request object
        git_dir (str): Local git directory path  
        system_prompt (str): System prompt for the AI
    """
    # Set up repository context for gh commands
    pr_number = pr.number
    repo_full_name = pr.base.repo.full_name
    
    # Build enhanced system prompt that leverages Claude's tools
    enhanced_system_prompt = f"""
{system_prompt}

You are a thorough code reviewer with access to GitHub CLI tools and file analysis capabilities. Your goal is to provide high-quality, evidence-based code review.

IMPORTANT GUIDELINES:
- Do NOT make comments based on assumptions or guesses
- Only comment on issues you can verify through actual code analysis
- Thoroughly analyze source code using Read and Grep tools before making any judgments
- Use web search if you need to verify best practices or library usage

REVIEW PROCESS:
1. First, gather comprehensive information:
   - Use `gh pr view {pr_number} -R {repo_full_name}` to get PR title and body
   - Use `gh pr diff {pr_number} -R {repo_full_name}` to get the patch diff
   - Use `gh pr view {pr_number} -R {repo_full_name} --comments` to get existing comments
   - Use Read and Grep tools to analyze the actual source code and understand context

2. Perform thorough analysis:
   - Read related source files to understand the full context
   - Verify imports, dependencies, and API usage
   - Check for actual bugs, not potential issues
   - Validate against established patterns in the codebase

3. Only provide feedback when you have concrete evidence:
   - Point to specific code lines and explain the exact issue
   - Provide evidence-based suggestions with reasoning
   - Reference documentation or established patterns when relevant

4. Post comments only for verified issues:
   - Use `gh pr comment {pr_number} -R {repo_full_name} --body "Your evidence-based comment here"`
   - If no concrete issues are found after thorough analysis, use `gh pr comment {pr_number} -R {repo_full_name} --body "LGTM - Code looks good after thorough review"`

PRIORITY:
- P1: Verified bugs with clear evidence. Reviewee must address these.
- P2: Verified improvements with solid reasoning. Reviewee should consider these.
- P3: Suggestions without strong evidence. Reviewee can choose to ignore these.
- Only post P1 ~ P3 comments, never lower priority.
"""

    try:
        async with ClaudeSDKClient(
            options=ClaudeCodeOptions(
                system_prompt=enhanced_system_prompt,
                allowed_tools=[
                    "Bash(gh pr view:*)",
                    "Bash(gh pr diff:*)", 
                    "Bash(gh pr comment:*)",
                    "Read",
                    "Grep",
                    "WebSearch"
                ],
                max_turns=40,
                cwd=git_dir
            )
        ) as client:
            
            # Send review request
            query_text = f"Please review pull request #{pr_number} in repository {repo_full_name}. Use the gh CLI tools to get the PR information and post your review comments directly using gh pr comment commands."
            
            await client.query(query_text)
            
            # Stream and print Claude's actions in real-time
            print("[INFO] Starting Claude Code review process...")
            response_text = ""
            async for message in client.receive_response():
                if hasattr(message, 'content'):
                    for block in message.content:
                        if hasattr(block, 'text'):
                            text_content = block.text
                            print(f"[CLAUDE] {text_content}", flush=True)
                            response_text += text_content
                        else:
                            print(f"[CLAUDE ACTION] {type(block).__name__}: {block}", flush=True)
                else:
                    print(f"[CLAUDE MESSAGE] {type(message).__name__}: {message}", flush=True)
            
            print(f"[INFO] Claude review completed. Full response length: {len(response_text)} characters")
            
    except Exception as e:
        print(f"[ERROR] Claude Code SDK error: {e}")

