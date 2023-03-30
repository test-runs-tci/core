import logging

import numpy as np
import pytest

from brainscore_core import Score as ScoreObject
from brainscore_core.benchmarks import BenchmarkBase
from brainscore_core.submission.database import (connect_db, reference_from_bibtex, benchmarkinstance_from_benchmark,
                                                 submissionentry_from_meta, modelentry_from_model, update_score,
                                                 public_model_identifiers, public_benchmark_identifiers)
from brainscore_core.submission.database_models import Score, BenchmarkType, Reference, clear_schema
from tests.test_submission import init_users

logger = logging.getLogger(__name__)

database = 'brainscore-ohio-test'  # test database

SAMPLE_BIBTEX = """@Article{Freeman2013,
                                author={Freeman, Jeremy and Ziemba, Corey M. and Heeger, David J. 
                                        and Simoncelli, Eero P. and Movshon, J. Anthony},
                                title={A functional and perceptual signature of the second visual area in primates},
                                journal={Nature Neuroscience},
                                year={2013},
                                month={Jul},
                                day={01},
                                volume={16},
                                number={7},
                                pages={974-981},
                                issn={1546-1726},
                                doi={10.1038/nn.3402},
                                url={https://doi.org/10.1038/nn.3402}
                                }"""


class SchemaTest:
    @classmethod
    def setup_class(cls):
        logger.info('Connect to database')
        connect_db(database)
        clear_schema()

    def setup_method(self):
        logger.info('Initialize database entries')
        init_users()

    def teardown_method(self):
        logger.info('Clean database')
        clear_schema()


def _mock_submission_entry():
    return submissionentry_from_meta(jenkins_id=123, user_id=1, model_type='base_model')


class TestModel(SchemaTest):
    def test_submission(self):
        entry = submissionentry_from_meta(jenkins_id=123, user_id=1, model_type='artificial_subject')
        assert entry.status == 'running'

    def test_model_no_bibtex(self):
        submission_entry = _mock_submission_entry()
        entry = modelentry_from_model(model_identifier='dummy', public=False, competition='cosyne2022',
                                      submission=submission_entry)
        with pytest.raises(Exception):
            entry.reference

    def test_model_with_bibtex(self):
        submission_entry = _mock_submission_entry()
        entry = modelentry_from_model(model_identifier='dummy', public=True, competition=None,
                                      submission=submission_entry, bibtex=SAMPLE_BIBTEX)
        assert entry.reference.year == '2013'


class _MockBenchmark(BenchmarkBase):
    def __init__(self):
        dummy_ceiling = ScoreObject(0.6)
        dummy_ceiling.attrs['error'] = 0.1
        super(_MockBenchmark, self).__init__(
            identifier='dummy', ceiling=dummy_ceiling, version=0, parent='neural')


class TestBenchmark(SchemaTest):
    def test_benchmark_instance_no_parent(self):
        benchmark = _MockBenchmark()
        instance = benchmarkinstance_from_benchmark(benchmark)
        type = BenchmarkType.get(identifier=instance.benchmark)
        assert instance.ceiling == 0.6
        assert instance.ceiling_error == 0.1
        assert not type.parent

    def test_benchmark_instance_existing_parent(self):
        # initially create the parent to see if the benchmark properly links to it
        BenchmarkType.create(identifier='neural', order=3)
        benchmark = _MockBenchmark()
        instance = benchmarkinstance_from_benchmark(benchmark)
        assert instance.benchmark.parent.identifier == 'neural'

    def test_reference(self):
        ref = reference_from_bibtex(SAMPLE_BIBTEX)
        assert isinstance(ref, Reference)
        assert ref.url == 'https://doi.org/10.1038/nn.3402'
        assert ref.year == '2013'
        assert ref.author is not None
        ref2 = reference_from_bibtex(SAMPLE_BIBTEX)
        assert ref2.id == ref.id


def _create_score_entry():
    submission_entry = submissionentry_from_meta(jenkins_id=123, user_id=1, model_type='brain_model')
    model_entry = modelentry_from_model(model_identifier='dummy', public=True, competition=None,
                                        submission=submission_entry)
    benchmark_entry = benchmarkinstance_from_benchmark(_MockBenchmark())
    entry, created = Score.get_or_create(benchmark=benchmark_entry, model=model_entry)
    return entry


class TestScore(SchemaTest):
    def test_score_no_ceiling(self):
        score = ScoreObject([.123, np.nan], coords={'aggregation': ['center', 'error']}, dims=['aggregation'])
        entry = _create_score_entry()
        update_score(score, entry)
        assert entry.score_ceiled is None
        assert np.isnan(entry.error)
        assert entry.score_raw == .123

    def test_score_with_ceiling(self):
        score = ScoreObject([.42, .1], coords={'aggregation': ['center', 'error']}, dims=['aggregation'])
        score.attrs['raw'] = ScoreObject([.336, .08], coords={'aggregation': ['center', 'error']}, dims=['aggregation'])
        score.attrs['ceiling'] = ScoreObject(.8)
        entry = _create_score_entry()
        update_score(score, entry)
        assert entry.score_ceiled == .42
        assert entry.error == .1
        assert entry.score_raw == .336

    def test_score_no_aggregation(self):
        score = ScoreObject(.42)
        entry = _create_score_entry()
        update_score(score, entry)
        assert entry.score_raw == .42
        assert entry.error is None

    def test_score_error_attr(self):
        score = ScoreObject(.42)
        score.attrs['error'] = .1
        entry = _create_score_entry()
        update_score(score, entry)
        assert entry.error == .1


class TestPublic(SchemaTest):
    def test_one_public_model(self):
        # create model
        submission_entry = _mock_submission_entry()
        modelentry_from_model(model_identifier='dummy', public=True, competition=None, submission=submission_entry)
        # test
        public_models = public_model_identifiers()
        assert public_models == ["dummy"]

    def test_one_public_one_private_model(self):
        # create models
        submission = _mock_submission_entry()
        modelentry_from_model(model_identifier='dummy_public', public=True, competition=None, submission=submission)
        modelentry_from_model(model_identifier='dummy_private', public=False, competition=None, submission=submission)
        # test
        public_models = public_model_identifiers()
        assert public_models == ["dummy_public"]

    def test_two_public_one_private_model(self):
        # create models
        submission = _mock_submission_entry()
        modelentry_from_model(model_identifier='dummy_public1', public=True, competition=None, submission=submission)
        modelentry_from_model(model_identifier='dummy_public2', public=True, competition=None, submission=submission)
        modelentry_from_model(model_identifier='dummy_private', public=False, competition=None, submission=submission)
        # test
        public_models = public_model_identifiers()
        assert set(public_models) == {"dummy_public1", "dummy_public2"}

    def test_one_public_benchmark(self):
        # create benchmarktype
        BenchmarkType.create(identifier='dummy', visible=True, order=1)
        # test
        public_benchmarks = public_benchmark_identifiers()
        assert public_benchmarks == ["dummy"]

    def test_one_public_one_private_benchmark(self):
        # create benchmarktypes
        BenchmarkType.create(identifier='dummy_public', visible=True, order=1)
        BenchmarkType.create(identifier='dummy_private', visible=False, order=1)
        # test
        public_benchmarks = public_benchmark_identifiers()
        assert public_benchmarks == ["dummy_public"]

    def test_two_public_two_private_benchmarks(self):
        # create benchmarktypes
        BenchmarkType.create(identifier='dummy_public1', visible=True, order=1)
        BenchmarkType.create(identifier='dummy_public2', visible=True, order=1)
        BenchmarkType.create(identifier='dummy_private1', visible=False, order=1)
        BenchmarkType.create(identifier='dummy_private2', visible=False, order=1)
        # test
        public_benchmarks = public_benchmark_identifiers()
        assert set(public_benchmarks) == {"dummy_public1", "dummy_public2"}