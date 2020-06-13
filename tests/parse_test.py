# Copyright 2009-2010 Yelp
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from subprocess import Popen, PIPE
from testify import TestCase, assert_equal, assert_not_equal

from mrjob.parse import *

class FindPythonTracebackTestCase(TestCase):

    def test_find_python_traceback(self):
        def run(*args):
            return Popen(args, stdout=PIPE, stderr=PIPE).communicate()

        # sanity-check normal operations
        ok_stdout, ok_stderr = run('python', '-c', "print sorted('321')")
        assert_equal(ok_stdout.rstrip(), "['1', '2', '3']")
        assert_equal(find_python_traceback(StringIO(ok_stderr)), None)

        # Oops, can't sort a number.
        stdout, stderr = run('python', '-c', "print sorted(321)")

        # We expect something like this:
        #
         # Traceback (most recent call last):
        #   File "<string>", line 1, in <module>
        # TypeError: 'int' object is not iterable
        assert_equal(stdout, '')
        # save the traceback for the next step
        tb = find_python_traceback(StringIO(stderr))
        assert_not_equal(tb, None)
        assert isinstance(tb, list)
        assert_equal(len(tb), 2) # The first line ("Traceback...") is skipped

        # make sure we can find the same traceback in noise
        verbose_stdout, verbose_stderr = run(
            'python', '-v', '-c', "print sorted(321)")
        assert_equal(verbose_stdout, '')
        assert_not_equal(verbose_stderr, stderr)
        verbose_tb = find_python_traceback(StringIO(verbose_stderr))
        assert_equal(verbose_tb, tb)

class FindMiscTestCase(TestCase):

    # we can't generate the output that the other find_*() methods look
    # for, so just search over some static data

    def test_empty(self):
        assert_equal(find_input_uri_for_mapper([]), None)
        assert_equal(find_hadoop_java_stack_trace([]), None)
        assert_equal(find_interesting_hadoop_streaming_error([]), None)

    def test_find_input_uri_for_mapper(self):
        LOG_LINES = [
            'garbage\n',
            "2010-07-27 17:54:54,344 INFO org.apache.hadoop.fs.s3native.NativeS3FileSystem (main): Opening 's3://yourbucket/logs/2010/07/23/log2-00077.gz' for reading\n",
            "2010-07-27 17:54:54,344 INFO org.apache.hadoop.fs.s3native.NativeS3FileSystem (main): Opening 's3://yourbucket/logs/2010/07/23/log2-00078.gz' for reading\n",
        ]
        assert_equal(
            find_input_uri_for_mapper(iter(LOG_LINES)),
            's3://yourbucket/logs/2010/07/23/log2-00077.gz',
        )

    def test_find_hadoop_java_stack_trace(self):
        LOG_LINES = [
            'java.lang.NameError: "Oak" was one character shorter\n',
            '2010-07-27 18:25:48,397 WARN org.apache.hadoop.mapred.TaskTracker (main): Error running child\n',
            'java.lang.OutOfMemoryError: Java heap space\n',
            '        at org.apache.hadoop.mapred.IFile$Reader.readNextBlock(IFile.java:270)\n',
            'BLARG\n',
            '        at org.apache.hadoop.mapred.IFile$Reader.next(IFile.java:332)\n',
        ]
        assert_equal(
            find_hadoop_java_stack_trace(iter(LOG_LINES)),
            [
                'java.lang.OutOfMemoryError: Java heap space\n',
                '        at org.apache.hadoop.mapred.IFile$Reader.readNextBlock(IFile.java:270)\n',
            ],
        )

    def test_find_interesting_hadoop_streaming_error(self):
        LOG_LINES = [
            '2010-07-27 19:53:22,451 ERROR org.apache.hadoop.streaming.StreamJob (main): Job not Successful!\n',
            '2010-07-27 19:53:35,451 ERROR org.apache.hadoop.streaming.StreamJob (main): Error launching job , Output path already exists : Output directory s3://yourbucket/logs/2010/07/23/ already exists and is not empty\n',
            '2010-07-27 19:53:52,451 ERROR org.apache.hadoop.streaming.StreamJob (main): Job not Successful!\n',
        ]

        assert_equal(
            find_interesting_hadoop_streaming_error(iter(LOG_LINES)),
            'Error launching job , Output path already exists : Output directory s3://yourbucket/logs/2010/07/23/ already exists and is not empty',
        )



class ParseMRJobStderr(TestCase):

    def test_empty(self):
        assert_equal(parse_mr_job_stderr(StringIO()),
                     {'counters': {}, 'statuses': [], 'other': []})

    def test_parsing(self):
        INPUT = StringIO(
            'reporter:counter:Foo,Bar,2\n' +
            'reporter:status:Baz\n' +
            'reporter:status:Baz\n' +
            'reporter:counter:Foo,Bar,1\n' +
            'reporter:counter:Foo,Baz,1\n' +
            'reporter:counter:Quux Subsystem,Baz,42\n' +
            'Warning: deprecated metasyntactic variable: garply\n')

        assert_equal(
            parse_mr_job_stderr(INPUT),
            {'counters': {'Foo': {'Bar': 3, 'Baz': 1},
                          'Quux Subsystem': {'Baz': 42}},
             'statuses': ['Baz', 'Baz'],
             'other': ['Warning: deprecated metasyntactic variable: garply\n']
            })

    def test_update_counters(self):
        counters = {'Foo': {'Bar': 3, 'Baz': 1}}

        parse_mr_job_stderr(
            StringIO('reporter:counter:Foo,Baz,1\n'), counters=counters)

        assert_equal(counters, {'Foo': {'Bar': 3, 'Baz': 2}})

    def test_read_single_line(self):
        # LocalMRJobRunner runs parse_mr_job_stderr on one line at a time.
        assert_equal(parse_mr_job_stderr('reporter:counter:Foo,Bar,2\n'),
                     {'counters': {'Foo': {'Bar': 2}},
                      'statuses': [], 'other': []})

    def test_read_multiple_lines_from_buffer(self):
        assert_equal(parse_mr_job_stderr('reporter:counter:Foo,Bar,2\nwoot\n'),
                     {'counters': {'Foo': {'Bar': 2}},
                      'statuses': [], 'other': ['woot\n']})

    def test_negative_counters(self):
        # kind of poor practice to use negative counters, but Hadoop
        # Streaming supports it (negative numbers are integers too!)
        assert_equal(parse_mr_job_stderr(['reporter:counter:Foo,Bar,-2\n']),
                     {'counters': {'Foo': {'Bar': -2}},
                      'statuses': [], 'other': []})

    def test_garbled_counters(self):
        # we should be able to do something graceful with
        # garbled counters and status messages
        BAD_LINES = [
            'reporter:counter:Foo,Bar,Baz,1\n', # too many items
            'reporter:counter:Foo,1\n', # too few items
            'reporter:counter:Foo,Bar,a million\n', # not a number
            'reporter:counter:Foo,Bar,1.0\n', # not an int
            'reporter:crounter:Foo,Bar,1\n', # not a valid reporter
            'reporter,counter:Foo,Bar,1\n', # wrong format!
        ]

        assert_equal(parse_mr_job_stderr(BAD_LINES),
                     {'counters': {}, 'statuses': [], 'other': BAD_LINES})

