# Copyright (C) 2026 by Tobias Pletsch

# This file is part of Open5GS.

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

libotel_file_list = '''
    ogs-otel.h
    ogs-otel.c
'''

libotel_dependencies = [libcore_dep, libcrypt_dep]

if get_option('otel')
    # opentelemetry-cpp (>= 1.16, static PIC build, OTLP/HTTP exporter)
    # installed under otel_prefix. Its CMake package exports no pkg-config
    # file for the OTLP exporter, so the archives are linked explicitly (meson
    # wraps link_args in --start-group/--end-group itself).
    otel_prefix = get_option('otel_prefix')

    otel_cpp_dep = declare_dependency(
        compile_args : ['-I' + join_paths(otel_prefix, 'include')],
        link_args : [
            '-L' + join_paths(otel_prefix, 'lib'),
            '-lopentelemetry_exporter_otlp_http',
            '-lopentelemetry_exporter_otlp_http_client',
            '-lopentelemetry_otlp_recordable',
            '-lopentelemetry_http_client_curl',
            '-lopentelemetry_proto',
            '-lopentelemetry_trace',
            '-lopentelemetry_logs',
            '-lopentelemetry_metrics',
            '-lopentelemetry_resources',
            '-lopentelemetry_common',
            '-lopentelemetry_version',
        ],
        dependencies : [
            dependency('protobuf'),
            dependency('libcurl'),
            dependency('threads'),
        ])

    libotel_dependencies += [otel_cpp_dep]
    libotel_file_list += ' ogs-otel-sdk.cpp'
else
    libotel_file_list += ' ogs-otel-void.c'
endif

libotel_sources = files(libotel_file_list.split())

libotel_inc = include_directories('.')

libotel = library('ogsotel',
    sources : libotel_sources,
    version : libogslib_version,
    c_args : '-DOGS_OTEL_COMPILATION',
    cpp_args : ['-std=c++17'],
    include_directories : [libotel_inc, libinc],
    dependencies : libotel_dependencies,
    install_rpath : libdir,
    install : true)

libotel_dep = declare_dependency(
    link_with : libotel,
    include_directories : [libotel_inc, libinc],
    dependencies : [libcore_dep, libcrypt_dep])
