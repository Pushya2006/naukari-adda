from django.contrib.auth import get_user_model
from django.test import TestCase

from jobs.models import Application, Job

from .models import Question, QuizAnswer, QuizAttempt

User = get_user_model()


def make_questions(role, count, correct=1):
    questions = []
    for index in range(count):
        questions.append(Question.objects.create(
            role=role,
            text=f'{role} question {index}',
            option1='a', option2='b', option3='c', option4='d',
            correct_option=correct,
        ))
    return questions


class QuizFlowTests(TestCase):

    def setUp(self):
        self.recruiter = User.objects.create_user(
            username='rec', password='pw12345!', user_type='recruiter',
            email='r@example.com')
        self.student = User.objects.create_user(
            username='stu', password='pw12345!', user_type='student',
            email='s@example.com')

        self.job = Job.objects.create(
            title='Python Dev', company='Acme', description='d',
            role='Python', required_skills='Python',
            min_score_required=3, posted_by=self.recruiter)

        make_questions('Python', 8)
        self.client.login(username='stu', password='pw12345!')

    def _answer_all(self, choice='1'):
        """Walk the whole quiz, answering every question with `choice`."""
        response = self.client.get('/quiz/')
        for _ in range(5):
            question_id = response.context['question'].id
            response = self.client.post('/quiz/', {
                'question_id': question_id,
                'answer': choice,
            })
        return response

    def test_quiz_requires_starting_from_a_job(self):
        response = self.client.get('/quiz/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/jobs/', response['Location'])

    def test_start_quiz_sets_session(self):
        self.client.get(f'/quiz/{self.job.id}/')
        self.assertEqual(self.client.session['job_id'], self.job.id)

    def test_five_questions_are_served(self):
        self.client.get(f'/quiz/{self.job.id}/')
        response = self.client.get('/quiz/')
        self.assertEqual(response.context['total'], 5)

    def test_all_correct_makes_student_eligible(self):
        self.client.get(f'/quiz/{self.job.id}/')
        response = self._answer_all('1')

        self.assertEqual(response.context['score'], 5)
        application = Application.objects.get(
            student=self.student, job=self.job)
        self.assertEqual(application.status, Application.STATUS_ELIGIBLE)

    def test_all_wrong_makes_student_not_eligible(self):
        self.client.get(f'/quiz/{self.job.id}/')
        response = self._answer_all('2')

        self.assertEqual(response.context['score'], 0)
        application = Application.objects.get(
            student=self.student, job=self.job)
        self.assertEqual(application.status,
                         Application.STATUS_NOT_ELIGIBLE)

    def test_scoring_is_accurate(self):
        self.client.get(f'/quiz/{self.job.id}/')
        response = self.client.get('/quiz/')

        expected = 0
        for index in range(5):
            question = response.context['question']
            answer = '1' if index < 3 else '4'   # 3 correct, 2 wrong
            if int(answer) == question.correct_option:
                expected += 1
            response = self.client.post('/quiz/', {
                'question_id': question.id,
                'answer': answer,
            })

        self.assertEqual(response.context['score'], expected)
        self.assertEqual(expected, 3)

    def test_only_one_attempt_per_job(self):
        self.client.get(f'/quiz/{self.job.id}/')
        self._answer_all('1')

        # Second attempt on the same job is refused.
        response = self.client.get(f'/quiz/{self.job.id}/', follow=True)
        self.assertEqual(
            QuizAttempt.objects.filter(
                user=self.student, job=self.job).count(), 1)
        self.assertContains(response, 'already attempted')

    def test_attempt_is_stored_with_job_link(self):
        self.client.get(f'/quiz/{self.job.id}/')
        self._answer_all('1')
        attempt = QuizAttempt.objects.get(user=self.student, job=self.job)
        self.assertEqual(attempt.total, 5)

    def test_answers_are_recorded(self):
        self.client.get(f'/quiz/{self.job.id}/')
        self._answer_all('1')
        self.assertEqual(QuizAnswer.objects.filter(
            user=self.student).count(), 5)

    def test_out_of_range_answer_is_ignored(self):
        self.client.get(f'/quiz/{self.job.id}/')
        response = self.client.get('/quiz/')
        question_id = response.context['question'].id

        self.client.post('/quiz/', {
            'question_id': question_id,
            'answer': '99',            # not a real option
        })

        self.assertNotIn(str(question_id),
                         self.client.session.get('answers', {}))

    def test_answer_for_a_different_question_is_ignored(self):
        other = make_questions('DevOps', 1)[0]
        self.client.get(f'/quiz/{self.job.id}/')
        self.client.get('/quiz/')

        self.client.post('/quiz/', {
            'question_id': other.id,   # not part of this quiz
            'answer': '1',
        })

        self.assertNotIn(str(other.id),
                         self.client.session.get('answers', {}))

    def test_questions_do_not_leak_between_jobs(self):
        """
        Regression: abandoning job A's quiz used to leave A's questions in
        the session, so job B served the wrong role's questions.
        """
        devops_job = Job.objects.create(
            title='DevOps Eng', company='Acme', description='d',
            role='DevOps', required_skills='Docker',
            min_score_required=3, posted_by=self.recruiter)
        devops_questions = make_questions('DevOps', 6)
        devops_ids = {q.id for q in devops_questions}

        # Start (but abandon) the Python quiz.
        self.client.get(f'/quiz/{self.job.id}/')
        self.client.get('/quiz/')

        # Now start the DevOps quiz.
        self.client.get(f'/quiz/{devops_job.id}/')
        response = self.client.get('/quiz/')

        self.assertIn(response.context['question'].id, devops_ids)

    def test_role_with_no_questions_is_handled(self):
        empty_job = Job.objects.create(
            title='Analyst', company='Acme', description='d',
            role='Data Analyst', required_skills='SQL',
            min_score_required=3, posted_by=self.recruiter)

        self.client.get(f'/quiz/{empty_job.id}/')
        response = self.client.get('/quiz/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No quiz questions')

    def test_recruiter_cannot_take_quiz(self):
        self.client.logout()
        self.client.login(username='rec', password='pw12345!')
        response = self.client.get(f'/quiz/{self.job.id}/')
        self.assertEqual(response.status_code, 302)

    def test_quiz_page_contains_webcam_code(self):
        self.client.get(f'/quiz/{self.job.id}/')
        response = self.client.get('/quiz/')
        self.assertContains(response, 'getUserMedia')
        self.assertContains(response, 'camPreview')

    def test_quiz_page_keeps_timer(self):
        self.client.get(f'/quiz/{self.job.id}/')
        response = self.client.get('/quiz/')
        self.assertContains(response, 'id="timer"')
