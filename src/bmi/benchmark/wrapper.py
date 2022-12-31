"""Allows running an external estimator."""
import abc
import pathlib
import subprocess
from typing import Literal, Optional, Sequence

from bmi.benchmark._serialize import TaskDirectory
from bmi.benchmark.core import RunResult, TaskMetadata
from bmi.benchmark.timer import Timer
from bmi.interface import Pathlike


def _run_command_and_read_mi(args: list[str]) -> float:
    raw_output = subprocess.check_output(args)
    output: str = raw_output.decode().strip()

    try:
        mi_estimate = float(output)
    except Exception as e:
        raise ValueError(f"Failed to cast output to float, got output '{output}' and error '{e}'.")

    return mi_estimate


def run_external_estimator(
    task_path: Pathlike,
    seed: int,
    command_args: Sequence[str],
    estimator_id: str,
    estimator_params: Optional[dict] = None,
    additional_args: Sequence[str] = (),
) -> RunResult:
    """Runs an external estimator via a command line.

    The estimator should take the following CLI arguments:

        COMMAND ARG_1 ... ARG_N  SAMPLES_CSV SEED DIM_X DIM_Y  ADDITIONAL_ARG1 ADDITIONAL_ARG2
        |______________________| |__________________________|  |______________________________|
              command_args          task-dependent params               additional_args
                                    added by this command

        and *print a single float* to the standard output.

        For example, an estimator `some_estimator.sh` which takes
        one hyperparameter --hyper (in this case 1524)
        can be run on task1/samples.csv with seed 42
        and with X dimension 5 and Y dimension 2 by:
        some_estimator.sh task1/samples.csv 42 5 2 --hyper 1524

    Args:
        task_path: path to the directory with a given task
        seed: random seed to select the right samples
        command_args: arguments necessary to run the estimator
          (without the parameters dependent on the task,
          i.e., the samples CSV, seed, and dimensions)
        estimator_id: the unique identifier of the estimator (and its parameters)
        estimator_params: dictionary with parameters of the estimator, to be added to the RunResult
        additional_args: method hyperparameters added after the task-dependent parameters
    """
    task_directory = TaskDirectory(task_path)
    metadata = TaskMetadata(**task_directory.load_metadata())

    our_args = [
        str(task_directory.samples),
        str(seed),
        str(metadata.dim_x),
        str(metadata.dim_y),
    ]

    timer = Timer()
    mi_estimate = _run_command_and_read_mi(list(command_args) + our_args + list(additional_args))

    return RunResult(
        task_id=metadata.task_id,
        seed=seed,
        estimator_id=estimator_id,
        mi_estimate=mi_estimate,
        time_in_seconds=timer.check(),
        estimator_params=estimator_params,
    )


class ExternalEstimator(abc.ABC):
    def __init__(self, estimator_id: Optional[str]) -> None:
        self._estimator_id = estimator_id

    def _estimator_params(self) -> Optional[dict]:
        return None

    @abc.abstractmethod
    def _precommands(self) -> list[str]:
        raise NotImplementedError

    def _postcommands(self) -> list[str]:
        return []

    @abc.abstractmethod
    def _default_estimator_id(self) -> str:
        raise NotImplementedError

    def estimator_id(self) -> str:
        if self._estimator_id is not None:
            return self._estimator_id
        else:
            return self._default_estimator_id()

    def estimate(self, task_path: Pathlike, seed: int) -> RunResult:
        return run_external_estimator(
            task_path=task_path,
            seed=seed,
            command_args=self._precommands(),
            estimator_id=self.estimator_id(),
            additional_args=self._postcommands(),
            estimator_params=self._estimator_params(),
        )


# Absolute path to the directory with external estimators
_EXTERNAL_ESTIMATORS_PATH: pathlib.Path = (
    pathlib.Path(__file__).parent.parent.parent.parent / "external"
)

# *String* representing the path to the R script calculating MI.
_PATH_TO_R_SCRIPT: str = str(_EXTERNAL_ESTIMATORS_PATH / "rmi.R")


class REstimatorKSG(ExternalEstimator):
    """The KSG estimators implemented in R."""

    def __init__(
        self, estimator_id: Optional[str] = None, variant: Literal[1, 2] = 1, neighbors: int = 10
    ) -> None:
        """

        Args:
            variant: 1 corresponds to KSG1, 2 corresponds to KSG2
            neighbors: number of neighbors (k) to be used

        Raises:
            FileNotFoundError, if the R script cannot be localized
        """
        super().__init__(estimator_id=estimator_id)

        if not pathlib.Path(_PATH_TO_R_SCRIPT).exists():
            raise FileNotFoundError(f"Path to the R script {_PATH_TO_R_SCRIPT} does not exist.")

        if neighbors < 1:
            raise ValueError(f"Neighbors {neighbors} must be at least 1.")

        self._variant = variant
        self._neighbors = neighbors
        self._estimator_id_param = estimator_id

    def _default_estimator_id(self) -> str:
        return f"REstimator-KSG{self._variant}-{self._neighbors}_neighbors"

    def _precommands(self) -> list[str]:
        return ["Rscript", _PATH_TO_R_SCRIPT]

    def _postcommands(self) -> list[str]:
        return ["--method", f"KSG{self._variant}", "--neighbors", str(self._neighbors)]

    def _estimator_params(self) -> dict:
        return {
            "neighbors": self._neighbors,
            "variant": self._variant,
        }


class REstimatorLNN(ExternalEstimator):
    """The LNN estimator implemented in R."""

    def __init__(
        self, estimator_id: Optional[str] = None, neighbors: int = 10, truncation: int = 30
    ) -> None:
        super().__init__(estimator_id=estimator_id)

        if neighbors < 1:
            raise ValueError(f"Neighbors must be at least 1, was {neighbors}.")
        if truncation < 1:
            raise ValueError(f"Truncation must be at least 1, was {truncation}.")

        self._neighbors = neighbors
        self._truncation = truncation

    def _default_estimator_id(self) -> str:
        return f"REstimator-LNN-{self._neighbors}_neighbors-{self._truncation}_truncation"

    def _precommands(self) -> list[str]:
        return ["Rscript", _PATH_TO_R_SCRIPT]

    def _postcommands(self) -> list[str]:
        return [
            "--method",
            "LNN",
            "--neighbors",
            str(self._neighbors),
            "--truncation",
            str(self._truncation),
        ]

    def _estimator_params(self) -> dict:
        return {"neighbors": self._neighbors, "truncation": self._truncation}