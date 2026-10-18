"""Pin BOLT11 route configuration to one path; preserve upstream router/scorer/retry."""
from pathlib import Path
import sys


def _replace_exact(source, old, new, count=1):
    if source.count(old) != count:
        raise ValueError(f"Expected {count} occurrence(s), found {source.count(old)}")
    return source.replace(old, new)


def single_path(source):
    old = "                RouteParametersConfig::default(),\n                Retry::Timeout(Duration::from_secs(10)),"
    if source.count(old) != 1:
        raise ValueError("Pinned BOLT11 payment call changed; single-path enforcement cannot be verified")
    return source.replace(old, "                RouteParametersConfig::default().with_max_path_count(1),\n                Retry::Timeout(Duration::from_secs(10)),")


def fix_rgb_htlc_accounting(rln_root):
    rgb_utils_path = rln_root / "rust-lightning/lightning/src/rgb_utils/mod.rs"
    channel_path = rln_root / "rust-lightning/lightning/src/ln/channel.rs"
    if not rgb_utils_path.exists() or not channel_path.exists():
        return

    rgb = rgb_utils_path.read_text()
    rgb = _replace_exact(
        rgb,
        "pub(crate) fn color_commitment<SP: Deref>(\n\tchannel_context: &ChannelContext<SP>, funding_scope: &FundingScope,\n\tcommitment_transaction: &mut CommitmentTransaction, counterparty: bool,\n) -> Result<(), ChannelError>",
        "pub(crate) fn color_commitment<SP: Deref>(\n\tchannel_context: &ChannelContext<SP>, funding_scope: &FundingScope,\n\tcommitment_transaction: &mut CommitmentTransaction, counterparty: bool,\n\trgb_balances: (u64, u64),\n) -> Result<(), ChannelError>",
    )
    rgb = _replace_exact(
        rgb,
        "\tlet chan_id = channel_id.0.as_hex();\n\tlet mut rgb_offered_htlc = 0;\n\tlet mut rgb_received_htlc = 0;\n\tlet mut last_rgb_payment_info = None;\n\tlet mut output_map = HashMap::new();",
        "\tlet chan_id = channel_id.0.as_hex();\n\tlet mut rgb_offered_htlc = 0;\n\tlet mut rgb_received_htlc = 0;\n\tlet mut output_map = HashMap::new();",
    )
    rgb = _replace_exact(
        rgb,
        """\t\tlet rgb_payment_info = if rgb_payment_info_proxy_id_path.exists() {
\t\t\tparse_rgb_payment_info(&rgb_payment_info_proxy_id_path)
\t\t} else {
\t\t\tlet rgb_payment_info = RgbPaymentInfo {
\t\t\t\tcontract_id,
\t\t\t\tamount: htlc_amount_rgb,
\t\t\t\tlocal_rgb_amount: rgb_info.local_rgb_amount,
\t\t\t\tremote_rgb_amount: rgb_info.remote_rgb_amount,
\t\t\t\tswap_payment: true,
\t\t\t\tinbound,
\t\t\t};
\t\t\tlet serialized_info =
\t\t\t\tserde_json::to_string(&rgb_payment_info).expect("valid rgb payment info");
\t\t\tfs::write(rgb_payment_info_proxy_id_path, serialized_info.clone())
\t\t\t\t.expect("able to write rgb payment info file");
\t\t\tfs::write(rgb_payment_info_path, serialized_info)
\t\t\t\t.expect("able to write rgb payment info file");
\t\t\trgb_payment_info
\t\t};

\t\tif inbound {
\t\t\trgb_received_htlc += rgb_payment_info.amount
\t\t} else {
\t\t\trgb_offered_htlc += rgb_payment_info.amount
\t\t};

\t\toutput_map.insert(htlc_vout, rgb_payment_info.amount);

\t\tlast_rgb_payment_info = Some(rgb_payment_info);
\t}

\tlet (local_amt, remote_amt) = if let Some(last_rgb_payment_info) = last_rgb_payment_info {
\t\t(
\t\t\tlast_rgb_payment_info.local_rgb_amount - rgb_offered_htlc,
\t\t\tlast_rgb_payment_info.remote_rgb_amount - rgb_received_htlc,
\t\t)
\t} else {
\t\t(rgb_info.local_rgb_amount, rgb_info.remote_rgb_amount)
\t};""",
        """\t\tif !rgb_payment_info_proxy_id_path.exists() {
\t\t\tlet rgb_payment_info = RgbPaymentInfo {
\t\t\t\tcontract_id,
\t\t\t\tamount: htlc_amount_rgb,
\t\t\t\tlocal_rgb_amount: rgb_info.local_rgb_amount,
\t\t\t\tremote_rgb_amount: rgb_info.remote_rgb_amount,
\t\t\t\tswap_payment: true,
\t\t\t\tinbound,
\t\t\t};
\t\t\tlet serialized_info =
\t\t\t\tserde_json::to_string(&rgb_payment_info).expect("valid rgb payment info");
\t\t\tfs::write(rgb_payment_info_proxy_id_path, serialized_info.clone())
\t\t\t\t.expect("able to write rgb payment info file");
\t\t\tif !rgb_payment_info_path.exists() {
\t\t\t\tfs::write(rgb_payment_info_path, serialized_info)
\t\t\t\t\t.expect("able to write rgb payment info file");
\t\t\t}
\t\t}

\t\tif inbound {
\t\t\trgb_received_htlc += htlc_amount_rgb
\t\t} else {
\t\t\trgb_offered_htlc += htlc_amount_rgb
\t\t};

\t\toutput_map.insert(htlc_vout, htlc_amount_rgb);
\t}

\tlet local_amt = rgb_balances.0.checked_sub(rgb_offered_htlc).ok_or_else(|| {
\t\tChannelError::close("Insufficient RGB for outbound HTLC outputs".to_owned())
\t})?;
\tlet remote_amt = rgb_balances.1.checked_sub(rgb_received_htlc).ok_or_else(|| {
\t\tChannelError::close("Insufficient RGB for inbound HTLC outputs".to_owned())
\t})?;""",
    )
    rgb_utils_path.write_text(rgb)

    s = channel_path.read_text()
    s = _replace_exact(
        s,
        "struct CommitmentData<'a> {\n\ttx: CommitmentTransaction,\n\thtlcs_included:",
        "struct CommitmentData<'a> {\n\ttx: CommitmentTransaction,\n\trgb_balances: (u64, u64),\n\thtlcs_included:",
    )
    s = _replace_exact(
        s,
        'color_commitment(&self.context(), &self.funding(), &mut commitment_data.tx, false).expect("successful commitment coloring");',
        'color_commitment(&self.context(), &self.funding(), &mut commitment_data.tx, false, commitment_data.rgb_balances).expect("successful commitment coloring");',
    )
    s = _replace_exact(
        s,
        "color_commitment(&self.context(), &self.funding(), &mut commitment_data.tx, true).unwrap();",
        "color_commitment(&self.context(), &self.funding(), &mut commitment_data.tx, true, commitment_data.rgb_balances).unwrap();",
    )
    s = _replace_exact(
        s,
        "\t/// Get the channel RGB capacity\n\tpub fn get_rgb_capacity(&self) -> u64 {\n\t\tself.get_local_rgb_amount() + self.get_remote_rgb_amount()\n\t}",
        """\t/// Get the channel RGB capacity
\tpub fn get_rgb_capacity(&self) -> u64 {
\t\tself.get_local_rgb_amount() + self.get_remote_rgb_amount()
\t}

\tpub(crate) fn available_outbound_rgb(&self) -> u64 {
\t\tif !self.is_colored()
\t\t\t|| !get_rgb_channel_info_path(
\t\t\t\t&self.channel_id.0.as_hex().to_string(),
\t\t\t\t&self.ldk_data_dir,
\t\t\t\ttrue,
\t\t\t)
\t\t\t.exists()
\t\t{
\t\t\treturn 0;
\t\t}
\t\tlet (info, _) = get_rgb_channel_info_pending(&self.channel_id, &self.ldk_data_dir);
\t\tlet offered = self
\t\t\t.pending_outbound_htlcs
\t\t\t.iter()
\t\t\t.filter_map(|htlc| htlc.rgb_payment.map(|(_, amount)| amount))
\t\t\t.chain(self.holding_cell_htlc_updates.iter().filter_map(|update| match update {
\t\t\t\tHTLCUpdateAwaitingACK::AddHTLC { rgb_payment: Some((_, amount)), .. } => {
\t\t\t\t\tSome(*amount)
\t\t\t\t},
\t\t\t\t_ => None,
\t\t\t}))
\t\t\t.fold(0u64, u64::saturating_add);
\t\tinfo.local_rgb_amount.saturating_sub(offered)
\t}

\tpub(crate) fn available_inbound_rgb(&self) -> u64 {
\t\tif !self.is_colored()
\t\t\t|| !get_rgb_channel_info_path(
\t\t\t\t&self.channel_id.0.as_hex().to_string(),
\t\t\t\t&self.ldk_data_dir,
\t\t\t\ttrue,
\t\t\t)
\t\t\t.exists()
\t\t{
\t\t\treturn 0;
\t\t}
\t\tlet (info, _) = get_rgb_channel_info_pending(&self.channel_id, &self.ldk_data_dir);
\t\tlet received = self
\t\t\t.pending_inbound_htlcs
\t\t\t.iter()
\t\t\t.filter_map(|htlc| htlc.rgb_payment.map(|(_, amount)| amount))
\t\t\t.fold(0u64, u64::saturating_add);
\t\tinfo.remote_rgb_amount.saturating_sub(received)
\t}""",
    )
    s = _replace_exact(
        s,
        '\t\tif self.is_colored() {\n\t\t\tcolor_commitment(&self, &funding, &mut commitment_data.tx, false)\n\t\t\t\t.expect("successful commitment coloring");\n\t\t}',
        '\t\tif self.is_colored() {\n\t\t\tcolor_commitment(\n\t\t\t\t&self,\n\t\t\t\t&funding,\n\t\t\t\t&mut commitment_data.tx,\n\t\t\t\tfalse,\n\t\t\t\tcommitment_data.rgb_balances,\n\t\t\t)\n\t\t\t.expect("successful commitment coloring");\n\t\t}',
    )
    s = _replace_exact(
        s,
        "\t\tlet mut value_to_self_claimed_msat = 0;\n\t\tlet mut value_to_remote_claimed_msat = 0;",
        "\t\tlet mut value_to_self_claimed_msat = 0;\n\t\tlet mut value_to_remote_claimed_msat = 0;\n\t\tlet mut value_to_self_claimed_rgb = 0u64;\n\t\tlet mut value_to_remote_claimed_rgb = 0u64;",
    )
    s = _replace_exact(
        s,
        "\t\t\t\tif let Some(preimage) = htlc.state.preimage() {\n\t\t\t\t\tinbound_htlc_preimages.push(preimage);\n\t\t\t\t\tvalue_to_self_claimed_msat += htlc.amount_msat;\n\t\t\t\t}",
        "\t\t\t\tif let Some(preimage) = htlc.state.preimage() {\n\t\t\t\t\tinbound_htlc_preimages.push(preimage);\n\t\t\t\t\tvalue_to_self_claimed_msat += htlc.amount_msat;\n\t\t\t\t\tvalue_to_self_claimed_rgb += htlc.rgb_payment.map_or(0, |(_, amount)| amount);\n\t\t\t\t}",
    )
    s = _replace_exact(
        s,
        "\t\t\t\tif htlc.state.preimage().is_some() {\n\t\t\t\t\tvalue_to_remote_claimed_msat += htlc.amount_msat;\n\t\t\t\t}",
        "\t\t\t\tif htlc.state.preimage().is_some() {\n\t\t\t\t\tvalue_to_remote_claimed_msat += htlc.amount_msat;\n\t\t\t\t\tvalue_to_remote_claimed_rgb += htlc.rgb_payment.map_or(0, |(_, amount)| amount);\n\t\t\t\t}",
    )
    s = _replace_exact(
        s,
        "\t\tlet value_to_self_msat = (funding.value_to_self_msat + value_to_self_claimed_msat).checked_sub(value_to_remote_claimed_msat).unwrap();",
        """\t\tlet value_to_self_msat = (funding.value_to_self_msat + value_to_self_claimed_msat).checked_sub(value_to_remote_claimed_msat).unwrap();
\t\tlet rgb_balances = if self.is_colored() {
\t\t\tlet (info, _) = get_rgb_channel_info_pending(&self.channel_id, &self.ldk_data_dir);
\t\t\tlet delta = value_to_self_claimed_rgb as i128 - value_to_remote_claimed_rgb as i128;
\t\t\t(
\t\t\t\tu64::try_from(info.local_rgb_amount as i128 + delta).expect("valid local RGB balance"),
\t\t\t\tu64::try_from(info.remote_rgb_amount as i128 - delta).expect("valid remote RGB balance"),
\t\t\t)
\t\t} else { (0, 0) };""",
    )
    s = _replace_exact(
        s,
        "\t\tCommitmentData {\n\t\t\ttx,\n\t\t\thtlcs_included,",
        "\t\tCommitmentData {\n\t\t\ttx,\n\t\t\trgb_balances,\n\t\t\thtlcs_included,",
    )
    s = _replace_exact(
        s,
        '\t\tif self.is_colored() {\n\t\t\tcolor_commitment(&self, &funding, &mut commitment_data.tx, true)\n\t\t\t\t.expect("successful commitment coloring");\n\t\t}',
        '\t\tif self.is_colored() {\n\t\t\tcolor_commitment(\n\t\t\t\t&self,\n\t\t\t\t&funding,\n\t\t\t\t&mut commitment_data.tx,\n\t\t\t\ttrue,\n\t\t\t\tcommitment_data.rgb_balances,\n\t\t\t)\n\t\t\t.expect("successful commitment coloring");\n\t\t}',
    )
    s = _replace_exact(
        s,
        "\t\tif let Some((_, amount_rgb)) = msg.rgb_payment {\n\t\t\tif amount_rgb > self.context.get_remote_rgb_amount() {",
        "\t\tif let Some((_, amount_rgb)) = msg.rgb_payment {\n\t\t\tif amount_rgb > self.context.available_inbound_rgb() {",
    )
    s = _replace_exact(
        s,
        """\t\tlet mut counterparty_commitment_tx = self
\t\t\t.context
\t\t\t.build_commitment_transaction(
\t\t\t\tpending_splice_funding,
\t\t\t\tself.context.counterparty_next_commitment_transaction_number + 1,
\t\t\t\t&self.context.counterparty_current_commitment_point.unwrap(),
\t\t\t\tfalse,
\t\t\t\tfalse,
\t\t\t\tlogger,
\t\t\t)
\t\t\t.tx;
\t\tif self.context.is_colored() {
\t\t\tcolor_commitment(
\t\t\t\t&self.context,
\t\t\t\t&pending_splice_funding,
\t\t\t\t&mut counterparty_commitment_tx,
\t\t\t\ttrue,
\t\t\t)
\t\t\t.expect("successful commitment coloring");
\t\t}""",
        """\t\tlet mut counterparty_commitment_data = self.context.build_commitment_transaction(
\t\t\tpending_splice_funding,
\t\t\tself.context.counterparty_next_commitment_transaction_number + 1,
\t\t\t&self.context.counterparty_current_commitment_point.unwrap(),
\t\t\tfalse,
\t\t\tfalse,
\t\t\tlogger,
\t\t);
\t\tif self.context.is_colored() {
\t\t\tcolor_commitment(
\t\t\t\t&self.context,
\t\t\t\t&pending_splice_funding,
\t\t\t\t&mut counterparty_commitment_data.tx,
\t\t\t\ttrue,
\t\t\t\tcounterparty_commitment_data.rgb_balances,
\t\t\t)
\t\t\t.expect("successful commitment coloring");
\t\t}
\t\tlet counterparty_commitment_tx = counterparty_commitment_data.tx;""",
    )
    s = _replace_exact(
        s,
        "\t\t\t\t\tif let &InboundHTLCRemovalReason::Fulfill(_, _) = reason {\n\t\t\t\t\t\tvalue_to_self_msat_diff += htlc.amount_msat as i64;\n\t\t\t\t\t}",
        "\t\t\t\t\tif let &InboundHTLCRemovalReason::Fulfill(_, _) = reason {\n\t\t\t\t\t\tvalue_to_self_msat_diff += htlc.amount_msat as i64;\n\t\t\t\t\t\trgb_received_htlc += htlc.rgb_payment.map_or(0, |(_, amount)| amount);\n\t\t\t\t\t}",
    )
    s = _replace_exact(
        s,
        "\t\t\t\t\t\t\t// They fulfilled, so we sent them money\n\t\t\t\t\t\t\tvalue_to_self_msat_diff -= htlc.amount_msat as i64;\n\t\t\t\t\t\t},",
        "\t\t\t\t\t\t\t// They fulfilled, so we sent them money\n\t\t\t\t\t\t\tvalue_to_self_msat_diff -= htlc.amount_msat as i64;\n\t\t\t\t\t\t\trgb_offered_htlc += htlc.rgb_payment.map_or(0, |(_, amount)| amount);\n\t\t\t\t\t\t},",
    )
    s = _replace_exact(
        s,
        """\t\t\t\t\tif let InboundHTLCState::AwaitingRemoteRevokeToAnnounce(resolution) = state {
\t\t\t\t\t\tlog_trace!(logger, " ...promoting inbound AwaitingRemoteRevokeToAnnounce {} to AwaitingAnnouncedRemoteRevoke", &htlc.payment_hash);
\t\t\t\t\t\thtlc.state = InboundHTLCState::AwaitingAnnouncedRemoteRevoke(resolution);
\t\t\t\t\t\trequire_commitment = true;

\t\t\t\t\t\tif let Some((_, amount_rgb)) = htlc.rgb_payment {
\t\t\t\t\t\t\trgb_received_htlc += amount_rgb;
\t\t\t\t\t\t}
\t\t\t\t\t}""",
        """\t\t\t\t\tif let InboundHTLCState::AwaitingRemoteRevokeToAnnounce(resolution) = state {
\t\t\t\t\t\tlog_trace!(logger, " ...promoting inbound AwaitingRemoteRevokeToAnnounce {} to AwaitingAnnouncedRemoteRevoke", &htlc.payment_hash);
\t\t\t\t\t\thtlc.state = InboundHTLCState::AwaitingAnnouncedRemoteRevoke(resolution);
\t\t\t\t\t\trequire_commitment = true;
\t\t\t\t\t}""",
    )
    s = _replace_exact(
        s,
        """\t\t\t\tif let OutboundHTLCState::LocalAnnounced(_) = htlc.state {
\t\t\t\t\tlog_trace!(
\t\t\t\t\t\tlogger,
\t\t\t\t\t\t" ...promoting outbound LocalAnnounced {} to Committed",
\t\t\t\t\t\t&htlc.payment_hash
\t\t\t\t\t);
\t\t\t\t\thtlc.state = OutboundHTLCState::Committed;
\t\t\t\t\tif let Some((_, amount_rgb)) = htlc.rgb_payment {
\t\t\t\t\t\trgb_offered_htlc += amount_rgb;
\t\t\t\t\t}
\t\t\t\t\t*expecting_peer_commitment_signed = true;
\t\t\t\t}""",
        """\t\t\t\tif let OutboundHTLCState::LocalAnnounced(_) = htlc.state {
\t\t\t\t\tlog_trace!(
\t\t\t\t\t\tlogger,
\t\t\t\t\t\t" ...promoting outbound LocalAnnounced {} to Committed",
\t\t\t\t\t\t&htlc.payment_hash
\t\t\t\t\t);
\t\t\t\t\thtlc.state = OutboundHTLCState::Committed;
\t\t\t\t\t*expecting_peer_commitment_signed = true;
\t\t\t\t}""",
    )
    s = _replace_exact(
        s,
        'color_commitment(&self.context, &self.funding, &mut commitment_data.tx, true).expect("successful commitment coloring");',
        'color_commitment(&self.context, &self.funding, &mut commitment_data.tx, true, commitment_data.rgb_balances).expect("successful commitment coloring");',
        count=2,
    )
    s = _replace_exact(
        s,
        "\t\tif let Some((_, amount_rgb)) = rgb_payment {\n\t\t\tlet local_rgb_amount = self.context.get_local_rgb_amount();",
        "\t\tif let Some((_, amount_rgb)) = rgb_payment {\n\t\t\tlet local_rgb_amount = self.context.available_outbound_rgb();",
    )
    s = _replace_exact(
        s,
        """\t\tlet mut rgb_received_htlc = 0;
\t\tfor htlc in self.context.pending_inbound_htlcs.iter_mut() {
\t\t\tlet new_state = if let &InboundHTLCState::AwaitingRemoteRevokeToAnnounce(ref forward_info) = &htlc.state {
\t\t\t\tSome(InboundHTLCState::AwaitingAnnouncedRemoteRevoke(forward_info.clone()))
\t\t\t} else { None };
\t\t\tif let Some(state) = new_state {
\t\t\t\tlog_trace!(logger, " ...promoting inbound AwaitingRemoteRevokeToAnnounce {} to AwaitingAnnouncedRemoteRevoke", &htlc.payment_hash);
\t\t\t\thtlc.state = state;

\t\t\t\tif let Some((_, amount_rgb)) = htlc.rgb_payment {
\t\t\t\t\trgb_received_htlc += amount_rgb;
\t\t\t\t}
\t\t\t}
\t\t}""",
        """\t\tfor htlc in self.context.pending_inbound_htlcs.iter_mut() {
\t\t\tlet new_state = if let &InboundHTLCState::AwaitingRemoteRevokeToAnnounce(ref forward_info) = &htlc.state {
\t\t\t\tSome(InboundHTLCState::AwaitingAnnouncedRemoteRevoke(forward_info.clone()))
\t\t\t} else { None };
\t\t\tif let Some(state) = new_state {
\t\t\t\tlog_trace!(logger, " ...promoting inbound AwaitingRemoteRevokeToAnnounce {} to AwaitingAnnouncedRemoteRevoke", &htlc.payment_hash);
\t\t\t\thtlc.state = state;
\t\t\t}
\t\t}""",
    )
    s = _replace_exact(
        s,
        """\t\tif self.context.is_colored() && rgb_received_htlc > 0 {
\t\t\tupdate_rgb_channel_amount_pending(&self.context.channel_id, 0, rgb_received_htlc, &self.context.ldk_data_dir);
\t\t}""",
        "",
    )
    s = _replace_exact(
        s,
        "color_commitment(&self.context, &self.funding, &mut commitment_data.tx, true)?;",
        "color_commitment(&self.context, &self.funding, &mut commitment_data.tx, true, commitment_data.rgb_balances)?;",
    )
    s = _replace_exact(
        s,
        "color_commitment(&self.context, &self.funding, &mut commitment_data.tx, true).unwrap();",
        "color_commitment(&self.context, &self.funding, &mut commitment_data.tx, true, commitment_data.rgb_balances).unwrap();",
    )
    channel_path.write_text(s)


if __name__ == "__main__":
    path = Path(sys.argv[1])
    path.write_text(single_path(path.read_text()))
    fix_rgb_htlc_accounting(path.parents[1])
