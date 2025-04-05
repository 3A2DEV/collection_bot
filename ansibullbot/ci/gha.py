import logging
from datetime import datetime

from ansibullbot.utils.timetools import strip_time_safely
from ansibullbot.ci.base import BaseCI

LOGGER = logging.getLogger('ansibullbot.ci.gha')

class GitHubActionsCI(BaseCI):
    name = 'gha'

    def __init__(self, cachedir, iw):
        super().__init__(cachedir, iw)
        self.repo = iw.repo
        self.pr = iw.number

    def get_last_full_run_date(self):
        workflows = self.get_workflow_runs()
        if workflows:
            return strip_time_safely(workflows[0]['created_at'])
        return None

    def get_workflow_runs(self):
        runs = []
        if not self.pr:
            return runs
            
        try:
            # Get workflow runs for this PR
            runs = self.repo.get_workflow_runs(
                actor=self.iw.submitter,
                pull_request=self.pr,
                event='pull_request'
            )
            runs = [run for run in runs if run.head_sha == self.iw.pullrequest.head.sha]
            return runs
        except Exception as e:
            LOGGER.error(e)
            return []

    def get_test_results(self):
        results = []
        runs = self.get_workflow_runs()
        
        for run in runs:
            try:
                jobs = run.jobs()
                for job in jobs:
                    if job.conclusion == 'failure':
                        logs = job.logs().decode('utf-8')
                        
                        # Determine test type from job name
                        test_type = None
                        if 'sanity' in job.name.lower():
                            test_type = 'sanity'
                        elif 'units' in job.name.lower():
                            test_type = 'units' 
                        elif 'integration' in job.name.lower():
                            test_type = 'integration'
                            
                        if test_type:
                            results.append({
                                'job': job.name,
                                'url': job.html_url,
                                'test_type': test_type,
                                'output': self._parse_ansible_test_output(logs, test_type),
                                'created_at': run.created_at
                            })
            except Exception as e:
                LOGGER.error(e)
                continue
                
        return results

    def _parse_ansible_test_output(self, logs, test_type):
        """Parse ansible-test output based on test type"""
        results = []
        in_error_section = False
        error_block = []
        
        for line in logs.split('\n'):
            # Sanity test errors
            if test_type == 'sanity':
                if 'ERROR: ' in line:
                    results.append(line.strip())
                elif 'Test failed' in line:
                    results.append(line.strip())
                    
            # Unit test errors
            elif test_type == 'units':
                if 'FAILED' in line:
                    in_error_section = True
                    error_block = [line.strip()]
                elif in_error_section:
                    if line.strip():
                        error_block.append(line.strip())
                    else:
                        in_error_section = False
                        results.append('\n'.join(error_block))
                        error_block = []
                        
            # Integration test errors
            elif test_type == 'integration':
                if 'fatal:' in line.lower():
                    results.append(line.strip())
                elif 'failed!' in line.lower():
                    results.append(line.strip())
                    
        return '\n'.join(results)

    def format_test_results(self, results):
        """Format test results for PR comment"""
        comment = []
        for result in results:
            comment.append(f"### {result['job']}")
            comment.append(f"Test Type: {result['test_type']}")
            comment.append(f"Job URL: {result['url']}")
            comment.append('\n<details><summary>Test Output</summary>\n')
            comment.append('```')
            comment.append(result['output'])
            comment.append('```')
            comment.append('</details>\n')
            
        if comment:
            header = "### Ansible Test Results\n"
            header += "The following tests failed:\n\n"
            return header + '\n'.join(comment)
        return None

    @property
    def updated_at(self):
        runs = self.get_workflow_runs() 
        if runs:
            return runs[0].updated_at
        return None
        
    def state(self):
        runs = self.get_workflow_runs()
        if not runs:
            return None
            
        # Use most recent run status
        latest = runs[0]
        if latest.status == 'completed':
            if latest.conclusion == 'success':
                return 'success'
            return 'failure'
        return 'pending'
