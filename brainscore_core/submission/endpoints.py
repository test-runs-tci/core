"""
Process plugin submissions (data, metrics, benchmarks, models) and score models on benchmarks.
"""
import traceback
import logging
from abc import ABC
from datetime import datetime
from typing import List, Union

from brainscore_core import Benchmark, Score
from brainscore_core.submission import database_models
from brainscore_core.submission.database import connect_db, modelentry_from_model, submissionentry_from_meta, \
    benchmarkinstance_from_benchmark, update_score, public_model_identifiers, public_benchmark_identifiers

logger = logging.getLogger(__name__)


def process_zip_submission(zip_filepath: str):
    """
    Triggered when a zip file is submitted via the website.
    Opens a pull request on GitHub with the plugin contents of the zip file.
    The merge of this PR will potentially trigger `process_github_submission`.
    """
    pass  # TODO @Katherine


def process_github_submission():
    """
    Triggered when changed are merged to the GitHub repository, if those changes affect benchmarks or models.
    Starts parallel runs to score models on benchmarks (`run_scoring`).
    """
    pass  # TODO @Katherine


class DomainPlugins(ABC):
    """
    Interface for domain-specific model + benchmark loaders and the score method.
    """

    def load_model(self, model_identifier: str):
        raise NotImplementedError()

    def load_benchmark(self, benchmark_identifier: str) -> Benchmark:
        raise NotImplementedError()

    def score(self, model_identifier: str, benchmark_identifier: str) -> Score:
        raise NotImplementedError()


class RunScoringEndpoint:
    ALL_PUBLIC = "all_public"  """ key to reference models or benchmarks to all public entries """

    def __init__(self, domain_plugins: DomainPlugins, db_secret: str):
        self.domain_plugins = domain_plugins
        logger.info(f"Connecting to db using secret '{db_secret}'")
        connect_db(db_secret=db_secret)

    def __call__(self, models: Union[List[str], str], benchmarks: Union[List[str], str],
                 # TODO @Katherine: the following parameters likely need to be passed differently,
                 #  e.g. in a config file/environment variables
                 jenkins_id: int, user_id: int, model_type: str,
                 model_public: bool, competition: Union[None, str]):
        """
        Run the `models` on the `benchmarks`, and write resulting scores to the database.

        :param models: either a list of model identifiers or the string
            :attr:`~brainscore_core.submission.endpoints.RunScoringEndpoint.ALL_PUBLIC` to select all public models
        :param benchmarks: either a list of benchmark identifiers or the string
            :attr:`~brainscore_core.submission.endpoints.RunScoringEndpoint.ALL_PUBLIC` to select all public benchmarks
        """
        # setup entry for this entire submission
        submission_entry = submissionentry_from_meta(jenkins_id=jenkins_id, user_id=user_id, model_type=model_type)
        entire_submission_successful = True

        # resolve settings
        if models == self.ALL_PUBLIC:
            models = public_model_identifiers()
        if benchmarks == self.ALL_PUBLIC:
            benchmarks = public_benchmark_identifiers()

        # iterate over all model-benchmark pairs
        for model_identifier in models:
            for benchmark_identifier in benchmarks:
                logger.info(f"Scoring {model_identifier} on {benchmark_identifier}")
                # TODO: I am worried about reloading models inside the loop. E.g. a keras model where layer names are
                #  automatic and will be consecutive from previous layers
                #  (e.g. on first load layers are [1, 2, 3], on second load layers are [4, 5, 6])
                #  which can lead to issues with layer assignment
                try:
                    self._score_model_on_benchmark(model_identifier=model_identifier,
                                                   benchmark_identifier=benchmark_identifier,
                                                   submission_entry=submission_entry, model_public=model_public,
                                                   competition=competition)
                except Exception as e:
                    entire_submission_successful = False
                    logging.error(
                        f'Could not run model {model_identifier} on benchmark {benchmark_identifier} because of {e}',
                        exc_info=True)

        # finalize status of submission
        submission_status = 'successful' if entire_submission_successful else 'failure'
        submission_entry.status = submission_status
        logger.info(f'Submission is stored as {submission_status}')
        submission_entry.save()

    def _score_model_on_benchmark(self, model_identifier: str, benchmark_identifier: str,
                                  submission_entry: database_models.Submission,
                                  model_public: bool, competition: Union[None, str]):
        # TODO: the following is somewhat ugly because we're afterwards loading model and benchmark again
        #  in the `score` method.
        logger.info(f'Model database entry')
        model = self.domain_plugins.load_model(model_identifier)
        model_entry = modelentry_from_model(model_identifier=model_identifier,
                                            submission=submission_entry, public=model_public, competition=competition,
                                            bibtex=model.bibtex if hasattr(model, 'bibtex') else None)
        logger.info(f'Benchmark database entry')
        benchmark = self.domain_plugins.load_benchmark(benchmark_identifier)
        benchmark_entry = benchmarkinstance_from_benchmark(benchmark)

        # Check if the model is already scored on the benchmark
        start_timestamp = datetime.now()
        score_entry, created = database_models.Score.get_or_create(benchmark=benchmark_entry, model=model_entry,
                                                                   defaults={'start_timestamp': start_timestamp, })
        if not created and score_entry.score_raw is not None:
            logger.warning(f'A score for model {model_identifier} and benchmark {benchmark_identifier} already exists')
            return

        if not created:  # previous score entry exists, but no score was stored
            score_entry.start_timestamp = datetime.now()
            score_entry.comment = None
            score_entry.save()
            logger.warning('A score entry exists but does not have a score value, so we run it again')

        # run actual scoring mechanism
        try:
            score_result = self.domain_plugins.score(
                model_identifier=model_identifier, benchmark_identifier=benchmark_identifier)
            score_entry.end_timestamp = datetime.now()
            # store in database
            logger.info(f'Score from running {model_identifier} on {benchmark_identifier}: {score_result}')
            update_score(score_result, score_entry)
        except Exception as e:
            stacktrace = traceback.format_exc()
            error_message = f'Model {model_identifier} could not run on benchmark {benchmark_identifier}: ' \
                            f'{repr(e)}. \n{stacktrace}'
            score_entry.comment = error_message[:database_models.Score.comment.max_length]
            score_entry.save()
            raise e
