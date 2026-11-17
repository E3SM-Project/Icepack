:tocdepth: 3

.. _testing:

Testing Icepack
================

This section documents primarily how to use the Icepack scripts to carry 
out icepack testing.  Exactly what to test is a separate question and
depends on the kinds of code changes being made.  Prior to merging
changes to the CICE Consortium main branch, changes will be reviewed and
developers will need to provide a summary of the tests carried out.

There is a base suite of tests provided by default with Icepack and this
may be a good starting point for testing.

An opt-in scientific mushy-layer suite is described in :ref:`mushy_testing`.

The testing scripts support several features
 - Ability to test individual (via ``--test``)or multiple tests (via ``--suite``)
   using an input file to define the suite or suites
 - Ability to use test suite defined in the package or test suites defined by the user
 - Ability to store test results for regresssion testing (``--bgen``)
 - Ability to compare results to prior baselines to verify bit-for-bit (``--bcmp``)
 - Ability to define where baseline tests are stored
 - Ability to compare tests against each other (``--diff``)

.. _indtests:

Individual Tests
----------------

The Icepack scripts support both setup of individual tests as well as test suites.  Individual
tests are run from the command line::

  ./icepack.setup --test smoke --mach conrad --env cray --set diag1,debug --testid myid 

Tests are just like cases but have some additional scripting around them.  Individual
tests can be created and manually modified just like cases.
Many of the command line arguments for individual tests
are similar to :ref:`case_options` for ``--case``.  
For individual tests, the following command line options can be set

``--test`` TESTNAME
     specifies the test type.  This is probably either smoke or restart but see `icepack.setup --help` for the latest.  This is required instead of ``--case``.

``--testid`` ID
     specifies the testid.  This is required for every use of ``--test`` and ``--suite``.  This is a user defined string that will allow each test to have a unique case and run directory name.  This is also required.

``--tdir`` PATH
     specifies the test directory.  Testcases will be created in this directory.  (default is .)

``--mach`` MACHINE (see :ref:`case_options`)

``--env`` ENVIRONMENT1 (see :ref:`case_options`)

``--set`` SET1,SET2,SET3 (see :ref:`case_options`)

``--acct`` ACCOUNT (see :ref:`case_options`)

``--grid`` GRID (see :ref:`case_options`)

``--pes`` MxN (see :ref:`case_options`)

Like ``--case``, ``--grid`` and ``--pes`` are not particularly
useful right now within Icepack since the model can only run serially and only
with the col grid setting.  
There are several additional options that come with ``--test`` that are not available
with ``--case`` for regression and comparision testing,

``--bdir`` DIR
     specifies the top level location of the baseline results.  This is used in conjuction with ``--bgen`` and ``--bcmp``.  The default is set by ICE_MACHINE_BASELINE in the env.[machine]_[environment] file.

``--bgen`` DIR
     specifies the name of the directory under [bdir] where test results will be stored.  When this flag is set, it automatically creates that directory and stores results from the test under that directory.  If DIR is set to ``default``, then the scripts will automatically generate a directory name based on the Icepack hash and the date and time.  This can be useful for tracking the baselines by hash.

``--bcmp`` DIR
     specifies the name of the directory under [bdir] that the current tests will be compared to.  When this flag is set, it automatically invokes regression testing and compares results from the current test to those prior results.  If DIR is set to ``default``, then the script will automatically generate the last directory name in the [bdir] directory.  This can be useful for automated regression testing.

``--diff`` LONG_TESTNAME
     invokes a comparison against another local test.  This allows different tests to be compared to each other.  The restrictions are that the test has to already be completed and the testid has to match.

The format of the case directory name for a test will always be 
``[machine]_[env]_[test]_[grid]_[pes]_[sets].[testid]``
The [sets] will always be sorted alphabetically by the script so ``--set debug,diag1`` and
``--set diag1,debug`` produces the same testname and test with _debug_diag1 in that order.

To build and run a test, the process is the same as a case.  cd to the 
test directory, run the build script, and run the submit script::

 cd [test_case]
 ./icepack.build
 ./icepack.submit

The test results will be generated in a local file called **test_output**.
To check those results::

 cat test_output

Tests are defined under **configuration/scripts/tests/**.  The tests currently supported are:

-  smoke   - Runs the model for default length.  The length and options can
            be set with the ``--set`` command line option.  The test passes if the
            model completes successfully.
-  restart - Runs the model for 14 months, writing a restart file at month 3 and
            again at the end of the run.  Runs the model a second time starting from the
            month 3 restart and writing a restart at month 12 of the model run.
            The test passes if both runs complete and
            if the restart files at month 12 from both runs are bit-for-bit identical.

Please run ``./icepack.setup --help`` for the latest information.


.. _examplediff:

Individual Test Examples
~~~~~~~~~~~~~~~~~~~~~~~~

 1) **Basic default single test**
     
    Define the test, mach, env, and testid.
    ::

      ./icepack.setup --test smoke --mach wolf --env gnu --testid t00
      cd wolf_gnu_smoke_col_1x1.t00
      ./icepack.build
      ./icepack.submit
      ./cat test_output


 2) **Simple test with some options**

    Add ``--set``
    ::

      ./icepack.setup --test smoke --mach wolf --env gnu --set diag1,debug --testid t00
      cd wolf_gnu_smoke_col_1x1_debug_diag1.t00
      ./icepack.build
      ./icepack.submit
      ./cat test_output


 3) **Single test, generate baseline dataset**

    Add ``--bgen``
    ::

      ./icepack.setup --test smoke --mach wolf -env gnu --bgen icepack.v01 --testid t00 --set diag1
      cd wolf_gnu_smoke_col_1x1_diag1.t00
      ./icepack.build
      ./icepack.submit
      ./cat test_output


 4) **Single test, compare results to a prior baseline**

    Add ``--bcmp``.  For this to work,
    the prior baseline must exist and have the exact same base testname 
    [machine]_[env]_[test]_[grid]_[pes]_[sets] 
    ::

      ./icepack.setup --test smoke --mach wolf -env gnu --bcmp icepack.v01 --testid t01 --set diag1
      cd wolf_gnu_smoke_col_1x1_diag1.t01
      ./icepack.build
      ./icepack.submit
      ./cat test_output


 5) **Simple test, generate a baseline dataset and compare to a prior baseline**

    Use ``--bgen`` and ``--bcmp``.  The prior baseline must exist already.
    ::

      ./icepack.setup --test smoke --mach wolf -env gnu --bgen icepack.v02 --bcmp icepack.v01 --testid t02 --set diag1
      cd wolf_gnu_smoke_col_1x1_diag1.t02
      ./icepack.build
      ./icepack.submit
      ./cat test_output


 6) **Simple test, comparison against another test**

    Use ``--diff``.  This feature is primarily used in test suites and has 
    limited use in icepack, but is being described for completeness.

    ``--diff`` provides a way to compare tests with each other.  
    For this to work, the tests have to be run in a specific order and
    the testids need to match.  The test 
    is always compared relative to the current case directory.

    To run the first test,
    ::

      ./icepack.setup --test smoke --mach wolf -env gnu --testid tx01 --set debug
      cd wolf_gnu_smoke_col_1x1_debug.tx01
      ./icepack.build
      ./icepack.submit
      ./cat test_output

    Then to run the second test and compare to the results from the first test
    ::

      ./icepack.setup --test smoke --mach wolf -env gnu --testid tx01 --diff smoke_col_1x1_debug
      cd wolf_gnu_smoke_col_1x1.tx01
      ./icepack.build
      ./icepack.submit
      ./cat test_output

    The scripts will add a [machine]_[environment] to the beginning of the diff 
    argument and the same testid to the end of the diff argument.  Then the runs 
    will be compared for bit-for-bit and a result will be produced in test_output.  
    This is really more useful in CICE and for test suites right now.  For example, 
    CICE uses this feature to compare results from different pe counts or 
    decompositions, single threaded vs multi-threaded, and so forth.

.. _testsuites:

Test suites
------------

Test suites support running multiple tests specified via
an input file or files.  When invoking the test suite option (``--suite``) with **icepack.setup**,
all tests will be created, built, and submitted automatically under
a directory called testsuite.[testid].[$date] as part of involing the suite.
The test scripts provide an ability to reuse binaries between tests within
a test suite when possible.
Because the tests are built and submitted automatically, 
this feature does not allow for customization of cases or tests like
individual cases and tests do::

  ./icepack.setup --suite base_suite --mach wolf --env gnu --testid myid

Like an individual test, the ``--testid`` option must be specified and can be any 
string.  

If using the ``--tdir`` option, that directory must not exist before the script is run.  The tdir directory will be
created by the script and it will be populated by all tests as well as scripts that support the test suite::

  ./icepack.setup --suite base_suite --mach wolf --env gnu --testid myid --tdir /scratch/$user/testsuite.myid

Once the tests are complete, results can be checked by running the
results.csh script in the [suite_name].[testid]::

  cd testsuite.[testid]
  ./results.csh

The predefined test suites are defined under **configuration/scripts/tests** and 
the files defining the suites
have a suffix of .ts in that directory.  The format for the test suite file 
is relatively simple.  
It is a text file with white space delimited 
columns that define a handful of values in a specific order.  
The first column is the test name, the second the grid, the third the pe count, 
the fourth column is
the ``--set`` options and the fifth column is the ``--diff`` argument. 
(The grid and PEs columns are provided 
for compatibility with the similar CICE scripts.)  The fourth and fifth columns are 
optional.
Lines that begin with # or are blank are ignored.  For example,
::

   #Test   Grid  PEs  Sets                Diff
    smoke   col  1x1  diag1  
    smoke   col  1x1  diag1,run1year  smoke_col_1x1_diag1
    smoke   col  1x1  debug,run1year  
   restart  col  1x1  debug  
   restart  col  1x1  diag1  
   restart  col  1x1  pondlvl  
   restart  col  1x1  pondtopo  

The argument to ``--suite`` defines the test suite (.ts) filename or filenames and that argument 
can contain a path.  
**icepack.setup** 
will look for the filename in the local directory, in **configuration/scripts/tests/**, 
or in the path defined by the ``--suite`` option.

Because many of the command line options are specified in the input file, ONLY the
following options are valid for suites,

``--suite`` suitename1,suitename2
  required, input filename with comma delimited list of suite or suites

``--mach`` MACHINE
  required

``--env`` ENVIRONMENT1,ENVIRONMENT2
  strongly recommended

``--acct`` ACCOUNT
  optional

``--tdir`` PATH
  optional

``--testid`` ID
  required

``--bdir`` DIR
  optional, top level baselines directory and defined by default by ICE_MACHINE_BASELINE in **env.[machine]_[environment]**.

``--bgen`` DIR
  recommended, test output is copied to this directory under [bdir]

``--bcmp`` DIR
  recommended, test output are compared to prior results in this directory under [bdir]

``--report``
  This is only used by ``--suite`` and when set, invokes a script that sends the test results to the results page when all tests are complete.  Please see :ref:`testreporting` for more information.

``--coverage``
  When invoked, code coverage diagnostics are generated.  This will modify the build and reduce optimization and generate coverage reports using lcov or codecov tools.  General use is not recommended, this is mainly used as a diagnostic to periodically assess test coverage.  Please see :ref:`codecoverage` for more information.

Please see :ref:`case_options` and :ref:`indtests` for more details about how these options are used.


Test Suite Examples
~~~~~~~~~~~~~~~~~~~~~~~~

 1) **Basic test suite**
     
    Specify suite, mach, env, testid.
    ::

      ./icepack.setup --suite base_suite --mach conrad --env cray --testid v01a
      cd base_suite.v01a
      #wait for runs to complete
      ./results.csh

 2) **Basic test suite with user defined test directory**
     
    Specify suite, mach, env, testid.
    ::

      ./icepack.setup --suite base_suite --mach conrad --env cray --testid v01a --tdir /scratch/$user/ts.v01a
      cd /scratch/$user/ts.v01a
      #wait for runs to complete
      ./results.csh


 3) **Multiple test suites on multiple environments**

      Specify multiple envs.
      ::

        ./icepack.setup --suite base_suite,quick_suite --mach conrad --env cray,pgi,intel,gnu --testid v01a
        cd testsuite.v01a
        #wait for runs to complete
        ./results.csh

      The interface supports both multiple suites and multiple environments from a single
      command line invokation.  Each env or suite can also be run as a separate invokation 
      of `icepack.setup` but if that approach is taken, it is recommended that different testids be used.


 4) **Basic test suite, store baselines in user defined name**

      Add ``--bgen``
      ::

        ./icepack.setup --suite base_suite --mach conrad --env cray --testid v01a --bgen icepack.v01a
        cd testsuite.v01a
        #wait for runs to complete
        ./results.csh

      This will store the results in the default [bdir] directory under the subdirectory icepack.v01a.


 5) **Basic test suite, store baselines in user defined top level directory**

      Add ``--bgen`` and ``--bdir``
      ::

        ./icepack.setup --suite base_suite --mach conrad --env cray --testid v01a --bgen icepack.v01a --bdir /tmp/user/ICEPACK_BASELINES
        cd testsuite.v01a
        #wait for runs to complete
        ./results.csh

      This will store the results in /tmp/user/ICEPACK_BASELINES/icepack.v01a.


 6) **Basic test suite, store baselines in auto-generated directory**

      Add ``--bgen default``
      ::

        ./icepack.setup --suite base_suite --mach conrad --env cray --testid v01a --bgen default
        cd testsuite.v01a
        #wait for runs to complete
        ./results.csh

      This will store the results in the default [bdir] directory under a directory name generated by the script that includes the hash and date.


 7) **Basic test suite, compare to prior baselines**

      Add ``--bcmp``
      ::

        ./icepack.setup --suite base_suite --mach conrad --env cray --testid v02a --bcmp icepack.v01a
        cd testsuite.v02a
        #wait for runs to complete
        ./results.csh

      This will compare to results saved in the baseline [bdir] directory under
      the subdirectory icepack.v01a.  You can use other regression options as well
      (``--bdir`` and ``--bgen``)


 8) **Basic test suite, use of default string in regression testing**

      default is a special argument to ``--bgen`` and ``--bcmp``.  When used, the
      scripts will automate generation of the directories.  In the case of ``--bgen``,
      a unique directory name consisting of the hash and a date will be created.
      In the case of ``--bcmp``, the latest directory in [bdir] will automatically
      be specified.  This provides a number of useful features

       - the ``--bgen`` directory will be named after the hash automatically
       - the ``--bcmp`` will always find the most recent set of baselines
       - the ``--bcmp`` reporting will include information about the comparison directory
         name which will include hash information
       - automation can be invoked easily, especially if ``--bdir`` is used to separate
         results

      Imagine the case where the default settings are used and ``--bdir`` is used to 
      create a unique location.  You could easily carry out regular builds automatically via,
      ::

        set mydate = `date -u "+%Y%m%d"`
        git clone https://github.com/myfork/icepack icepack.$mydate
        cd icepack.$mydate
        ./icepack.setup --suite base_suite --mach conrad --env cray,gnu,intel,pgi --testid $mydate --bcmp default --bgen default --bdir /tmp/work/user/ICEPACK_BASELINES_MAIN

      When this is invoked, a new set of baselines will be generated and compared to the prior
      results each time without having to change the arguments.


 9) **Create and test a custom suite**

      Create your own input text file consisting of 5 columns of data,
       - Test
       - Grid
       - pes
       - sets (optional)
       - diff test (optional)

      such as
      ::

         > cat mysuite
         smoke    col  1x1  diag1,debug
         restart  col  1x1
         restart  col  1x1  diag1,debug    restart_col_1x1
         restart  col  1x1  mynewoption,diag1,debug

      then use that input file, mysuite
      ::

        ./icepack.setup --suite mysuite --mach conrad --env cray --testid v01a --bgen default
        cd mysuite.v01a
        #wait for runs to complete
        ./results.csh

      You can use all the standard regression testing options (``--bgen``, ``--bcmp``, 
      ``--bdir``).  Make sure any "diff" testing that goes on is on tests that
      are created earlier in the test list, as early as possible.  Unfortunately,
      there is still no absolute guarantee the tests will be completed in the correct 
      sequence.


.. _testreporting:

Test Reporting
---------------

The Icepack testing scripts have the capability to post test results
to the official `wiki page <https://github.com/CICE-Consortium/Test-Results/wiki>`_.
You may need write permission on the wiki.  If you are interested in using the
wiki, please contact the consortium.

To post results, once a test suite is complete, run ``results.csh`` and
``report_results.csh`` from the suite directory,
::

  ./icepack.setup --suite base_suite --mach conrad --env cray --testid v01a
  cd testsuite.v01a
  #wait for runs to complete
  ./results.csh
  ./report_results.csh

``report_results.csh`` will run ``results.csh`` by default automatically, but
we recommmend running it manually first to verify results before publishing
them.  ``report_results.csh -n`` will turn off automatic running of ``results.csh``.

The reporting can also be automated in a test suite by adding ``--report`` to ``icepack.setup``
::

  ./icepack.setup --suite base_suite --mach conrad --env cray --testid v01a --report

With ``--report``, the suite will create all the tests, build and submit them,
wait for all runs to be complete, and run the results and report_results scripts.

.. _codecoverage:

Code Coverage Testing
------------------------

The ``--coverage`` feature in **icepack.setup** provides a method to diagnose code coverage.
This argument turns on special compiler flags including reduced optimization and then
invokes the gcov tool.  Once runs are complete, either lcov or codecov can be used
to analyze the results.
This option is currently only available with the gnu compiler and on a few systems
with modified Macros files.

At the present time, the ``--coverage`` flag invokes the lcov analysis automatically
by running the **report_lcov.csh** script in the test suite directory.  The output 
will show up at the `CICE-Consortium code coverage website <https://apcraig.github.io>`__.  To
use the tool, you should have write permission for that repository.  The lcov tool
should be run on a full multi-suite test suite, and it can 
take several hours to process the data once the test runs are complete.  A typical
instantiation would be
::

  ./icepack.setup --suite base_suite,travis_suite,quick_suite --mach cheyenne --env gnu --testid cc01 --coverage

Alternatively, codecov analysis can be carried out by manually running the **report_codecov.csh**
script from the test suite directory, but there are several ongoing problems with this approach and
it is not generally recommended.  The codecov
analysis is largely identical to the analysis performed by lcov, codecov just provides a nicer 
web experience to view the output.

This is a special diagnostic test and is not part of the standard model testing.
General use is not recommended, this is mainly used as a diagnostic to periodically
assess test coverage.  


.. _testplotting:

Test Plotting
----------------

The Icepack scripts include a script (``timeseries.csh``) that will generate a timeseries 
figure from the diagnostic output file.  
When running a test suite, the ``timeseries.csh`` script is automatically copied to the suite directory.  
If the ``timeseries.csh`` script is to be used on a test / case that is not a part of a test suite, 
users will need to run the ``timeseries.csh`` script from the tests directory 
(``./configuration/scripts/tests/timeseries.csh``), or copy it to a local directory and run it 
locally (``cp configuration/scripts/tests/timeseries.csh .`` followed by 
``./timeseries.csh /path/to/ice_diag.full_ITD``. The plotting script can be run
on any of the output files - icefree, slab, full_ITD, land).  To generate the figure, 
run the ``timeseries.csh`` script and pass the full path to the ice_diag file as an argument.  

For example:

Run the test suite. ::

$ ./icepack.setup -m conrad -e intel --suite base_suite -acct <account_number> --testid t00

Wait for suite to finish then go to the directory. ::

$ cd testsuite.t00

Run the timeseries script on the desired case. ::

$ ./timeseries.csh /p/work1/turner/ICEPACK_RUNS/conrad_intel_smoke_col_1x1_diag1_run1year.t00/ice_diag.full_ITD
    
The output figures are placed in the directory where the ice_diag file is located.

This plotting script can be used to plot the following variables:

  - area fraction
  - average ice thickness (m)
  - average snow depth (m)
  - air temperature (C)
  - shortwave radiation (:math:`W/m^2`)
  - longwave radiation (:math:`W/m^2`)
  - snowfall
  - average salinity (ppt)
  - surface temperature (C)
  - outward longwave flux (:math:`W/m^2`)
  - sensible heat flux (:math:`W/m^2`)
  - latent heat flux (:math:`W/m^2`)
  - top melt (m)
  - bottom melt (m)
  - lateral melt (m)
  - new ice (m)
  - congelation (m)
  - snow-ice (m)
  - initial energy change (:math:`W/m^2`)

.. _mushy_testing:

Mushy-layer scientific validation
---------------------------------

``configuration/scripts/tests/test_mushy_layer.py`` is a self-contained Python
3.10+ tool.  It needs no third-party Python packages.  Model runs require a
working native Icepack machine environment, C/Fortran compilers, make, csh and
``/usr/bin/time``.  T2 also requires Git and an available baseline ref.
Downloads prefer system ``curl`` (with TLS verification); otherwise Python's
HTTPS client is used.  Configure the trusted CA bundle normally if necessary;
certificate checks must not be disabled.

The scientific formulation exercised by these tests, including the
enthalpy-dependent zero-salinity phase interval and the native frazil
salinity law, is documented in :ref:`thermo-mushy-zero-salinity` and
:ref:`ocean`. The suite covers both direct phase-state checks and integrated
driver behavior; it does not replace the standard smoke/restart tests.

The tool uses ``icepack.setup`` and the selected machine's environment/Macros.
It does not replace compiler settings, activate a fictitious environment, edit
physics sources, or rely on code outside this Icepack checkout.  Build/run
directories are private to a new output directory; native case paths must not
contain whitespace or shell metacharacters.  Do not run overlapping experiments
in the same output directory or edit the sources/tool during a run.

Standalone usage, from the checkout root::

  python3 configuration/scripts/tests/test_mushy_layer.py list
  python3 configuration/scripts/tests/test_mushy_layer.py self-test
  python3 configuration/scripts/tests/test_mushy_layer.py run \
    --machine conda --environment macos --suite core --output /new/core-results
  python3 configuration/scripts/tests/test_mushy_layer.py run \
    --machine conda --environment macos --suite extended --output /new/full-results
  python3 configuration/scripts/tests/test_mushy_layer.py run \
    --machine conda --environment macos --tests T1,T7,T10-brine \
    --offline --output /new/selected-results
  python3 configuration/scripts/tests/test_mushy_layer.py run \
    --machine conda --environment macos --tests T14 \
    --offline --output /new/paired-freshening-results

Replace the example machine/environment with a supported local pair.
``--icepack-root`` selects another checkout.  ``--tests`` overrides the suite;
``extended`` includes all core entries.  T2 compares a saline default-off
restart with a separately built ``--baseline-ref`` (default ``HEAD``).  Use the
pre-change commit explicitly when evaluating regression against earlier code.
The exact detached reference commit and source hashes are recorded.

The tool writes ``TEST_RESULTS.md``, SVG figures, measured-value tables, raw
CSV diagnostics/restarts, command/build logs, ``results.json`` and
``manifest.json``.  Existing output directories are rejected.  A failed command
or missing/nonfinite/malformed selected input is an error, never a skipped test
or a passing default.  Exit codes are 0 for satisfied selected acceptance
criteria (or completed descriptive studies), 1 for unmet acceptance/convergence
criteria, and 2 for input/build/execution errors.  An extended run can therefore
finish all experiments and return 1 for nonconvergence.

To verify evidence or regenerate its description::

  python3 configuration/scripts/tests/test_mushy_layer.py validate --results /new/full-results
  python3 configuration/scripts/tests/test_mushy_layer.py report --results /new/full-results

Validation checks required evidence layouts, hashes and recorded verdicts, and
independently recomputes conservation identities.  The manifest is not signed;
these checks do not authenticate results against coordinated modification.
Success means the checked evidence is consistent, not that every scientific
criterion passed.  Read the separate scientific verdicts in the report.
Reproduction from a different checkout/compiler must be a new run, not relabelled
historical evidence.
Results produced before the test renumbering use a legacy manifest schema;
validate or report those results with their original version of the runner.
The former T13, T14 and T15 cases are now T11, T12 and T13 respectively;
in particular, selecting T13 now runs the flag off/on sensitivity study.
Existing same-numbered schema-2 results remain readable, but only newly run
schema-3 T13 results include the layer-evolution comparisons described below.
The newly added T14 is the paired salinity/freezing-point-SST companion;
it is not the former, pre-renumbering T14 convergence case.
Schema-4 T14 checks the prescribed ocean boundary only, at 7 and 20 layers.
An earlier schema-3 T14 definition also graded layer refinement; keep those
results with their original runner rather than relabelling their verdicts.
The current validator explicitly rejects that earlier T14 definition.

Input preparation and offline execution
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Missing inputs needed by the selected tests are fetched automatically, pinned
to exact SHA-256 values in the script.  Synthetic core tests need no downloads.
Only T9 needs the observation table; T11--T14 need the sample forcing archive.
To prepare a compute node before disconnecting::

  python3 configuration/scripts/tests/test_mushy_layer.py fetch-data --suite extended \
    --cache-dir /private/icepack-input-cache
  python3 configuration/scripts/tests/test_mushy_layer.py run --suite extended \
    --machine MACHINE --environment ENVIRONMENT --offline \
    --cache-dir /private/icepack-input-cache --output /new/offline-results

The default cache is ``~/.cache/icepack/mushy``.  Verified cached archives/files
are reused; extracted archive members are checked again against the pinned
archive.  Downloads are bounded and atomically published only after checksum
verification.  Archive paths, links and special files are rejected.  No shared
``ICEPACK_DATA`` directory is overwritten.  A corrupt cache is an explicit
error: remove the identified private entry deliberately before refetching.
``--offline`` makes no network requests and fails if selected inputs are absent.
Prescribed-forcing runs stage verified copies under short local filenames in
the driver's ``NICE_2015`` layout, avoiding its fixed-length filename limit.

Attribution and exact upstream sources:

* Icepack sample data version 20200326:
  https://doi.org/10.5281/zenodo.3728287.  The archive's SHA-256 is
  ``eb706ec09d4d35d6da3a118c8cba79d7d727e3d6aed19568e24423b92d3ea6bc``.
  Upstream attribution remains in the extracted distribution.
* MOSAiC first-year ice-core table, CC BY 4.0:
  https://doi.org/10.1594/PANGAEA.971385.  The table's SHA-256 is
  ``f7a13410394126aac95a56e218988f7ee8a4e4abe512c0e7b618c520df350f81``.
  T9 selects PS122/1_5-3, sample PS122-1_5-FYI, 2019-10-27 23:46 UTC:
  eight sections over 0.420 m.  Practical salinity is treated numerically as
  ppt (a documented 1:1 approximation).

Scientific coverage and limits
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Entry
     - Scope
   * - T1
     - Seven exact freshwater cases and an 80-state analytical binary-mixture grid.
   * - T2
     - Eighteen compiled phase states, positive-salinity flag parity, 53 frazil
       salinities under two caps with enthalpy reconstruction, and a default-off
       saline restart comparison against the recorded ref.
   * - T3
     - Freshwater integration, split restart, and diagnostics-on/off equivalence.
   * - T4
     - One-step/two-step basal growth and salinity below the legacy floor.
   * - T5
     - 4/7/10/14/20/28-layer study, preserving the initial discrete mean salinity.
   * - T6
     - Active topographic-pond freshwater feedback and coupling identity.
   * - T7
     - Controlled permeability cutoff, flushing connectivity and derived
       freshwater, salt and enthalpy fluxes.
   * - T8
     - Whole-cell water/salt, nonvacuous zero-SSS frazil with zero new-frazil
       salinity/correction salt, zero/tiny-salinity comparison and local heat.
   * - T9
     - Conservative observed-profile adaptation, not a transient hindcast.
   * - T10-brine
     - Active-brine zero/tiny-salinity flag pairs, seven controlled profiles,
       and public-API replay of integrated freshwater profiles.
   * - T10-BGC
     - Active-tracer initializer, tiny-positive scaling and zero-denominator guard.
   * - T10
     - A 365-day flag off/on pair plus eleven paired one-day namelist families.
   * - T10-cost
     - 7/20 ice layers, five snow layers, four-cell debug and I/O-included timing.
   * - T11
     - Two-year prescribed control/salinity-notch sensitivity at 7/20 ice layers.
   * - T12
     - The same forcing at 7/10/14/20/28 layers with fixed refinement criteria.
   * - T13
     - Flag off/on prescribed-forcing sensitivity at 7/20 ice layers.
   * - T14
     - Companion control/freshening experiments with actual SST following the
       salinity-dependent freezing point, both flags, and 7/20 ice layers.
       Layer refinement is not a T14 acceptance requirement.

The pinned extended settings are an example of a current Icepack configuration
used in Earth system models, not a coupled-model validation.  T11--T13 repeat a
365-day forcing year twice and introduce six Gaussian salinity notches with exact-zero daily
centres; other numerical ocean fields remain unchanged.
Their actual SST stays at its initialized -1.8 C because both
``oceanmixed_ice`` and ``restore_ocn`` are false. These salinity-only cases
remain separate from the paired salinity/temperature experiment below.

T14 repeats the same atmosphere and ocean salinity treatments, but enables
the standalone ``sst_at_freezing`` control with ``tfrz_option='mushy'``.
At each forcing update, actual SST is set to Icepack's freezing temperature
at the interpolated SSS; it is not inferred from an unused SST input file or
approximated by slow restoring. Control SST also follows its own freezing
point, so the control is not identical to the fixed-temperature T11--T13
control. Ocean mixed-layer evolution and restoring remain off. This is a
prescribed boundary experiment, not a closed ocean heat-budget calculation.
The zero-salinity physics option remains off by default; T14 explicitly runs
both settings. As in T12, initial ice profiles preserve the seven-layer
reference mean salinity at every resolution.

Eight two-year integrations cross both ocean treatments, both flag settings,
and two resolutions. Every timestep in both target cells must have actual
SST within 1e-12 C of the calculated freezing temperature. Freshening cases
must record exactly 12 samples per cell with zero SSS and exactly zero SST
and freezing temperature; controls must have no zero-SSS samples.
The ``T14-summary/summary.csv`` table records those event counts, actual
SST ranges, maximum tracking error, final ice volume, and integrated salt flux.
The ``T14-layers`` figures provide the same 28 daily layer comparisons as T13
at 7 and 20 layers. These off/on comparisons have no equivalence threshold.
T14 acceptance checks the actual boundary values and valid layer states,
not agreement between resolutions. Layer-refinement convergence remains
the responsibility of the separate convergence tests; it does not affect
T14's verdict.

T13 runs both forcing treatments with the zero-salinity option off and on,
at 7 and 20 ice layers.  Its ``T13-layers`` output compares the daily evolution
of all ice-layer temperatures, salinities and liquid fractions, all five
snow-layer temperatures, and ice and snow thicknesses.  For each forcing and
resolution there are plots for the slab cell, the full thickness-distribution
cell's volume-weighted mean, and each of its five categories.  Ice and snow
states are weighted by their respective category volumes; inactive categories
and absent snow appear as gaps rather than zero-valued states.  The summary
reports maximum off/on differences only where both runs have samples.  These
are descriptive comparisons, not equivalence or observational acceptance
criteria.

The integrated T1 grid does not independently assert all small-salinity
asymptotic and branch-boundary continuity checks represented in the archived
development evidence.  This is a coverage limitation of T1, not a dependency
on code outside the Icepack repository.

T5 compares 10 versus 14 layers on a common 101-point depth grid with fixed
limits of 0.1 C, 0.01 liquid fraction, 0.05 ppt and 2 percent basal growth.
T12 requires both temperature and net-salt-flux criteria for both cases and
target cells on two successive adjacent refinement pairs.  Limits are 0.1 C
and 2 percent relative L1 salt flux, with an absolute 1e-6 kg/m2 tolerance
when the fine-grid reference integral is at most 1e-6 kg/m2.  Failure is reported
as nonconvergence, not converted into acceptance by changing thresholds.

T8 uses 1e-8 kg/m2 whole-cell water/salt tolerances and 1.1e-3 W/m2 for local
thermodynamic heat.  The water inventory includes ice, snow and topographic
pond water, with explicit precipitation, evaporation, frazil and drainage
terms.  These checks do not close full coupled/pond energy or salt budgets.
BGC initializer checks do not establish later tracer evolution.  Cost includes
normal diagnostic/restart I/O but excludes environment startup from the
executable timing; it is not production throughput or a performance threshold.

For freshwater, phase acceptance is enthalpy-dependent: below
``-rhoi*Lfresh`` the state is solid; from that endpoint to zero enthalpy the
temperature is zero and liquid fraction is ``1 + q/(rhoi*Lfresh)``; above zero
enthalpy it is liquid.  Brine checks preserve tiny positive liquid fractions,
test exact solid impermeability, and compare remapped enthalpy and harmonic
permeability.  Integrated profiles are replayed through the supported
microstructure interface, not obtained by source instrumentation.

Native test integration and optional diagnostics
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The dedicated suite adds native freshwater smoke/restart configurations and
the scientific core test, without changing existing tests or default suites::

  ./icepack.setup --suite mushy_suite --mach MACHINE --env ENVIRONMENT --testid ID

An individual scientific test can also be created::

  ./icepack.setup --test mushy_layer --mach MACHINE --env ENVIRONMENT \
    --set debug,mushyfresh,run1day --testid ID

Use the normal native build/submit workflow.  The wrapper first runs a canonical
native case for ``baseline.script`` and then writes scientific ensemble evidence
under a unique ``${ICE_CASEDIR}/mushy-results.XXXXXX/results`` directory for
each invocation.  Standard native ``--bgen``/``--bcmp``
continue to operate on the canonical restart, separately from scientific
criteria.  Python is needed only for ``mushy_layer``, not existing smoke/restart
tests.  Extended studies remain explicit standalone selections.

Optional ``setup_nml`` variables ``mushy_diagnostics``, ``mushy_diagfreq`` and
``mushy_profilefreq`` control driver-owned CSV output.  They are off by default.
Positive sampling intervals select column samples and, additionally, ice/snow
profiles and local energy samples.  Files are ``mushy_columns.csv``,
``mushy_layers.csv``, ``mushy_snow.csv`` and ``mushy_energy.csv``; headers include
units. Column records include actual ``sst_C`` and
``freezing_temperature_C`` as well as ``sss_ppt``; these are used to verify
the paired boundary at every sampled timestep.
The energy interface returns per-category timestep, initial/final/input
energy, signed residual and absolute residual rate.  No diagnostic I/O is added
to column physics.  Test clients configure Icepack first, use public interfaces
with keyword arguments, and flush/check warning/abort state after calls, following
:ref:`dev_testing` and the public-interface protocols.

