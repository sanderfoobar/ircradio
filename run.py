# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2021, dsc@xmr.pm

import os
import pwd
import logging
import shutil

from quart import render_template
import click

from ircradio.factory import create_app
import settings


@click.group()
def cli():
    pass


@cli.command(name="generate")
def cli_generate_configs(*args, **kwargs):
    """Generate icecast2/liquidsoap configs and systemd service files"""
    from ircradio.utils import jinja2_render, write_file_sync, systemd_servicefile

    templates_dir = os.path.join(settings.cwd, "ircradio", "templates")

    # liquidsoap service file
    path_liquidsoap = shutil.which("liquidsoap")
    path_liquidsoap_config = os.path.join(settings.cwd, "data", "soap.liq")

    liquidsoap_systemd_service = systemd_servicefile(
        name="liquidsoap",
        description="liquidsoap service",
        user=pwd.getpwuid(os.getuid()).pw_name,
        group=pwd.getpwuid(os.getuid()).pw_name,
        path_executable=path_liquidsoap,
        args_executable=path_liquidsoap_config,
        env="")
    write_file_sync(fn=os.path.join(settings.cwd, "data", "liquidsoap.service"), data=liquidsoap_systemd_service)

    # liquidsoap config
    template = jinja2_render("soap.liq.jinja2",
                             icecast2_bind_host=settings.icecast2_bind_host,
                             icecast2_bind_port=settings.icecast2_bind_port,
                             liquidsoap_host=settings.liquidsoap_host,
                             liquidsoap_port=settings.liquidsoap_port,
                             icecast2_mount=settings.icecast2_mount,
                             liquidsoap_description=settings.liquidsoap_description,
                             icecast2_source_password=settings.icecast2_source_password,
                             dir_music=settings.dir_music)
    write_file_sync(fn=os.path.join(settings.cwd, "data", "soap.liq"), data=template.encode())

    # cross.liq
    path_liquidsoap_cross_template = os.path.join(templates_dir, "cross.liq.jinja2")
    path_liquidsoap_cross = os.path.join(settings.cwd, "data", "cross.liq")
    shutil.copyfile(path_liquidsoap_cross_template, path_liquidsoap_cross)

    # icecast2.xml
    template = jinja2_render("icecast.xml.jinja2",
                             icecast2_bind_host=settings.icecast2_bind_host,
                             icecast2_bind_port=settings.icecast2_bind_port,
                             hostname="localhost",
                             log_dir=settings.icecast2_logdir,
                             source_password=settings.icecast2_source_password,
                             relay_password=settings.icecast2_relay_password,
                             admin_password=settings.icecast2_admin_password,
                             dir_music=settings.dir_music)
    path_icecast2_config = os.path.join(settings.cwd, "data", "icecast.xml")
    write_file_sync(path_icecast2_config, data=template.encode())

    # nginx
    template = jinja2_render("nginx.jinja2",
                             icecast2_bind_host=settings.icecast2_bind_host,
                             icecast2_bind_port=settings.icecast2_bind_port,
                             hostname=settings.icecast2_hostname,
                             icecast2_mount=settings.icecast2_mount,
                             host=settings.host,
                             port=settings.port)
    path_nginx_config = os.path.join(settings.cwd, "data", "radio_nginx.conf")
    write_file_sync(path_nginx_config, data=template.encode())
    print(f"written config files to {os.path.join(settings.cwd, 'data')}")


@cli.command(name="webdev")
def webdev(*args, **kwargs):
    """Run the web-if, for development purposes"""
    from ircradio.factory import create_app
    app = create_app()
    app.run(settings.host, port=settings.port, debug=settings.debug, use_reloader=False)


if __name__ == '__main__':
    cli()
