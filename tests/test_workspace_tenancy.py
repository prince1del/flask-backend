"""Per-login private workspace assignment."""

from app.workspace_tenancy import (
    private_workspace_id,
    resolve_workspace_id_for_new_user,
)


def test_private_workspace_id_is_stable_slug():
    assert private_workspace_id('Rahul.Sharma') == 'exec_rahul_sharma'
    assert private_workspace_id('bd_gt_north_head') == 'exec_bd_gt_north_head'


def test_new_executive_gets_private_silo_by_default():
    assert resolve_workspace_id_for_new_user('alice', 'sales_executive') == 'exec_alice'
    assert resolve_workspace_id_for_new_user('bob', 'distributor') == 'exec_bob'


def test_explicit_workspace_kept_for_shared_team():
    assert (
        resolve_workspace_id_for_new_user(
            'alice',
            'sales_executive',
            'bombay_dyeing_gt_north',
        )
        == 'bombay_dyeing_gt_north'
    )


def test_admin_stays_on_default_without_explicit():
    assert resolve_workspace_id_for_new_user('founder', 'admin') == 'default'
