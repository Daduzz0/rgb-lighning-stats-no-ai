"""Add structured payment event logging; fail if the pinned source changed."""
from pathlib import Path
import sys


def instrument(source):
    marker = "            find_and_update_rgb_chan_amt(&static_state.ldk_data_dir, &payment_hash, false);"
    if source.count(marker) != 1:
        raise ValueError("Unexpected PaymentSent handler in upstream source")
    source = source.replace(marker, '''            tracing::info!("RGB_STATS_EVENT {}", serde_json::json!({
                "event": "payment_sent", "paymentHash": payment_hash.to_string(),
                "feeMsat": fee_paid_msat,
            }));
''' + marker)
    for name, hash_expression in (("Successful", "payment_hash.map(|hash| hash.to_string())"),
                                  ("Failed", "Some(payment_hash.to_string())")):
        old = f"        Event::PaymentPath{name} {{ .. }} => {{}}"
        if source.count(old) != 1:
            raise ValueError(f"Unexpected PaymentPath{name} handler")
        new = '''        Event::PaymentPathNAME { payment_hash, path, .. } => {
            let hops: Vec<_> = path.hops.iter().map(|hop| serde_json::json!({
                "pubkey": hop.pubkey.to_string(),
                "shortChannelId": hop.short_channel_id,
                "feeOrFinalAmountMsat": hop.fee_msat,
                "cltvExpiryDelta": hop.cltv_expiry_delta,
                "paymentAmount": hop.payment_amount,
                "rgbAssetId": hop.rgb_payment.as_ref().map(|(id, _)| id.to_string()),
                "rgbAmount": hop.rgb_payment.as_ref().map(|(_, amount)| *amount),
            })).collect();
            tracing::info!("RGB_STATS_EVENT {}", serde_json::json!({
                "event": "EVENT", "paymentHash": HASH,
                "feeMsat": path.fee_msat(), "amountMsat": path.final_value_msat(),
                "blinded": path.blinded_tail.is_some(), "hops": hops,
            }));
        }'''.replace("NAME", name).replace("EVENT\"", ("path_succeeded" if name == "Successful" else "path_failed") + '"').replace("HASH", hash_expression)
        source = source.replace(old, new)
    return source


if __name__ == "__main__":
    path = Path(sys.argv[1])
    path.write_text(instrument(path.read_text()))
