"""Click-based CLI for komoot2sigma."""

from __future__ import annotations

from pathlib import Path

import click

from komoot2sigma.config import (
    get_komoot_credentials,
    get_sigma_credentials,
    save_komoot_credentials,
)
from komoot2sigma.komoot import KomootClient
from komoot2sigma.sigma import SigmaCloudClient, guid_for_komoot_tour


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Transfer planned routes from Komoot to Sigma Data Cloud."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


@cli.group()
def login() -> None:
    """Authenticate with Komoot or Sigma Cloud."""
    pass


@login.command("komoot")
@click.option("--email", prompt="Komoot email", help="Your Komoot email.")
@click.option(
    "--password",
    prompt="Komoot password",
    hide_input=True,
    help="Your Komoot password.",
)
@click.pass_context
def login_komoot(ctx: click.Context, email: str, password: str) -> None:
    """Authenticate with Komoot and save session credentials."""
    verbose = ctx.obj["verbose"]
    client = KomootClient()

    if verbose:
        click.echo(f"Authenticating with Komoot as {email}...")

    user_id, token, display_name = client.login(email, password)
    save_komoot_credentials(user_id, token, display_name)

    click.echo(f"Logged in as {display_name} (user_id: {user_id})")


@login.command("sigma")
@click.option("--email", prompt="Sigma Cloud email", help="Your Sigma Cloud email.")
@click.option(
    "--password",
    prompt="Sigma Cloud password",
    hide_input=True,
    help="Your Sigma Cloud password.",
)
@click.pass_context
def login_sigma(ctx: click.Context, email: str, password: str) -> None:
    """Authenticate with Sigma Data Cloud."""
    verbose = ctx.obj["verbose"]
    client = SigmaCloudClient()
    if verbose:
        click.echo(f"Authenticating with Sigma Cloud as {email}...")
    access_token = client.authenticate(email, password)
    click.echo(f"Sigma Cloud authorized. Token: {access_token[:8]}...")


@cli.command("list")
@click.option(
    "--all-tours", is_flag=True, help="Show all tours, not just planned."
)
@click.pass_context
def list_tours(ctx: click.Context, all_tours: bool) -> None:
    """List available routes from Komoot."""
    verbose = ctx.obj["verbose"]
    komoot_creds = get_komoot_credentials()
    if not komoot_creds:
        raise click.ClickException(
            "Komoot credentials not found. Run `komoot2sigma login komoot` first."
        )

    client = KomootClient.from_credentials(
        komoot_creds["user_id"], komoot_creds["token"]
    )

    if verbose:
        click.echo("Fetching tours from Komoot...")

    if all_tours:
        tours = client.list_tours()
    else:
        tours = client.list_planned_tours()

    if not tours:
        click.echo("No planned tours found.")
        return

    click.echo(f"Found {len(tours)} tour(s):\n")
    for tour in tours:
        click.echo(f"  {tour.summary()}")


@cli.command("transfer")
@click.argument("tour_id", required=False)
@click.option("--all", "transfer_all", is_flag=True, help="Transfer all planned routes.")
@click.option("--dry-run", is_flag=True, help="Download only, don't upload to Sigma.")
@click.pass_context
def transfer(
    ctx: click.Context,
    tour_id: str | None,
    transfer_all: bool,
    dry_run: bool,
) -> None:
    """Transfer a route from Komoot to Sigma Cloud.

    Provide a TOUR_ID to transfer a specific tour, or use --all for all planned routes.
    """
    verbose = ctx.obj["verbose"]

    if not tour_id and not transfer_all:
        raise click.ClickException(
            "Provide a TOUR_ID or use --all to transfer all planned routes."
        )

    komoot_creds = get_komoot_credentials()
    if not komoot_creds:
        raise click.ClickException(
            "Komoot credentials not found. Run `komoot2sigma login komoot` first."
        )

    if not dry_run:
        sigma_creds = get_sigma_credentials()
        if not sigma_creds:
            raise click.ClickException(
                "Sigma credentials not found. Run `komoot2sigma login sigma` first."
            )
        sigma_client = SigmaCloudClient(sigma_creds["access_token"])
    else:
        sigma_client = None

    komoot_client = KomootClient.from_credentials(
        komoot_creds["user_id"], komoot_creds["token"]
    )

    if transfer_all:
        tours = komoot_client.list_planned_tours()
        if not tours:
            click.echo("No planned tours found.")
            return
        tour_ids = [t.tour_id for t in tours]
        tour_names = {t.tour_id: t.name for t in tours}
    else:
        tour_ids = [tour_id]  # type: ignore[list-item]
        all_tours = komoot_client.list_tours()
        tour_names = {t.tour_id: t.name for t in all_tours}

    success_count = 0
    for tid in tour_ids:
        name = tour_names.get(tid, f"Tour {tid}")
        click.echo(f"Downloading: {name} ({tid})...")

        gpx_data = komoot_client.download_gpx(tid)
        if verbose:
            click.echo(f"  Downloaded {len(gpx_data)} bytes of GPX data.")

        if dry_run:
            click.echo(f"  [dry-run] Would upload to Sigma Cloud.")
            success_count += 1
            continue

        guid = guid_for_komoot_tour(tid)
        click.echo(f"  Uploading to Sigma Cloud...")
        if sigma_client and sigma_client.upload_gpx(gpx_data, name, verbose, guid):
            click.echo(f"  Uploaded successfully.")
            success_count += 1
        else:
            click.echo(f"  Upload failed.")

    click.echo(f"\nDone: {success_count}/{len(tour_ids)} routes transferred.")


@cli.command("sync")
@click.option("--dry-run", is_flag=True, help="Show what would be synced without uploading.")
@click.pass_context
def sync(ctx: click.Context, dry_run: bool) -> None:
    """Sync planned routes from Komoot to Sigma Cloud.

    Only uploads routes that don't already exist on Sigma Cloud.
    """
    verbose = ctx.obj["verbose"]

    komoot_creds = get_komoot_credentials()
    if not komoot_creds:
        raise click.ClickException(
            "Komoot credentials not found. Run `komoot2sigma login komoot` first."
        )

    sigma_creds = get_sigma_credentials()
    if not sigma_creds:
        raise click.ClickException(
            "Sigma credentials not found. Run `komoot2sigma login sigma` first."
        )

    komoot_client = KomootClient.from_credentials(
        komoot_creds["user_id"], komoot_creds["token"]
    )
    sigma_client = SigmaCloudClient(sigma_creds["access_token"])

    click.echo("Fetching planned tours from Komoot...")
    tours = komoot_client.list_planned_tours()
    if not tours:
        click.echo("No planned tours found.")
        return

    click.echo(f"Found {len(tours)} planned tour(s) on Komoot.")

    click.echo("Querying existing routes on Sigma Cloud...")
    existing_guids = sigma_client.list_route_guids(verbose)
    if verbose:
        click.echo(f"  Found {len(existing_guids)} route(s) on Sigma Cloud.")

    to_upload = []
    for tour in tours:
        guid = guid_for_komoot_tour(tour.tour_id)
        if guid in existing_guids:
            if verbose:
                click.echo(f"  Already on Sigma: {tour.name} ({tour.tour_id})")
        else:
            to_upload.append(tour)

    if not to_upload:
        click.echo("All planned tours are already on Sigma Cloud. Nothing to do.")
        return

    click.echo(f"\n{len(to_upload)} tour(s) to upload:")
    for tour in to_upload:
        click.echo(f"  {tour.name} ({tour.distance_km:.1f} km)")

    if dry_run:
        click.echo("\n[dry-run] No routes uploaded.")
        return

    click.echo()
    success_count = 0
    for tour in to_upload:
        click.echo(f"Downloading: {tour.name} ({tour.tour_id})...")
        gpx_data = komoot_client.download_gpx(tour.tour_id)

        guid = guid_for_komoot_tour(tour.tour_id)
        click.echo(f"  Uploading to Sigma Cloud...")
        if sigma_client.upload_gpx(gpx_data, tour.name, verbose, guid):
            click.echo(f"  Uploaded successfully.")
            success_count += 1
        else:
            click.echo(f"  Upload failed.")

    click.echo(f"\nDone: {success_count}/{len(to_upload)} routes synced.")


@cli.command("upload")
@click.argument("gpx_file", type=click.Path(exists=True, path_type=Path))
@click.option("--name", help="Override route name (defaults to filename).")
@click.option("--dry-run", is_flag=True, help="Parse GPX but don't upload.")
@click.pass_context
def upload(
    ctx: click.Context,
    gpx_file: Path,
    name: str | None,
    dry_run: bool,
) -> None:
    """Upload a local GPX file to Sigma Cloud."""
    verbose = ctx.obj["verbose"]
    route_name = name or gpx_file.stem

    gpx_data = gpx_file.read_bytes()
    if verbose:
        click.echo(f"Read {len(gpx_data)} bytes from {gpx_file}")

    if dry_run:
        click.echo(f"[dry-run] Would upload '{route_name}' to Sigma Cloud.")
        return

    sigma_creds = get_sigma_credentials()
    if not sigma_creds:
        raise click.ClickException(
            "Sigma credentials not found. Run `komoot2sigma login sigma` first."
        )

    sigma_client = SigmaCloudClient(sigma_creds["access_token"])
    click.echo(f"Uploading '{route_name}' to Sigma Cloud...")

    if sigma_client.upload_gpx(gpx_data, route_name, verbose):
        click.echo("Upload successful.")
    else:
        raise click.ClickException(
            "Upload failed. Use --verbose for details."
        )
