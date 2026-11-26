import os
from collections import defaultdict

import httpx
import streamlit as st


BACKEND_URL = os.getenv("STREAMLIT_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")


def api(method: str, path: str, **kwargs):
    try:
        response = httpx.request(method, f"{BACKEND_URL}{path}", timeout=30.0, **kwargs)
    except httpx.HTTPError as exc:
        return None, f"Unable to reach API: {exc}"

    try:
        body = response.json()
    except ValueError:
        if "text/csv" in response.headers.get("content-type", ""):
            if response.is_error:
                return None, response.text or f"API request failed ({response.status_code})"
            return response.text, None
        body = {"error": response.text or "Unexpected API response"}

    if response.is_error:
        message = body.get("error") if isinstance(body, dict) else str(body)
        return None, message or f"API request failed ({response.status_code})"
    if method.upper() == "POST" and path == "/batches" and isinstance(body, dict):
        body = dict(body)
        body["_upload_status"] = "already_uploaded" if response.status_code == 200 else "new_batch"
    return body, None


def show_error(error):
    if error:
        st.error(error)


st.set_page_config(page_title="Grade Moderation Pipeline", layout="wide")
st.title("Grade Moderation Pipeline")

st.sidebar.caption(f"API: {BACKEND_URL}")

page = st.sidebar.radio(
    "Screen",
    ["Upload", "Batch list", "Batch detail", "Audit log"],
)

health, health_error = api("GET", "/health")
if health_error:
    st.error(f"Backend unavailable: {health_error}")
else:
    st.sidebar.success("Backend is available")

if page == "Upload":
    st.header("Upload CSV")
    uploaded = st.file_uploader("Choose a score CSV", type=["csv"])

    if uploaded is not None:
        if st.button("Upload", type="primary"):
            result, error = api(
                "POST",
                "/batches",
                files={"file": (uploaded.name, uploaded.getvalue(), "text/csv")},
            )
            show_error(error)
            if result:
                if result.get("_upload_status") == "already_uploaded":
                    st.success(f"This file was already uploaded. Batch {result['id']} returned.")
                else:
                    st.success(f"New batch created: batch {result['id']}")

                st.subheader("Batch")
                st.json(result)

if page == "Batch list":
    st.header("Batches")
    batches, error = api("GET", "/batches")
    show_error(error)
    if batches:
        for batch in batches:
            with st.expander(f"Batch {batch['id']} - {batch['original_filename']}"):
                st.write("Status:", batch.get("status") or "unknown")
                audit, audit_error = api("GET", f"/batches/{batch['id']}/audit")
                show_error(audit_error)
                if audit:
                    stage_events = [
                        event for event in audit
                        if event["event_type"] in {"STAGE_STARTED", "STAGE_COMPLETED", "STAGE_FAILED"}
                    ]
                    if stage_events:
                        st.write("Stage activity:")
                        for event in stage_events:
                            details = event.get("details") or {}
                            st.text(f"{details.get('stage', '?')}: {event['event_type']}")
                    else:
                        st.text("No stage activity yet")

                    stage_status = {}
                    for event in audit:
                        if event["event_type"] not in {"STAGE_STARTED", "STAGE_COMPLETED", "STAGE_FAILED"}:
                            continue
                        stage_name = (event.get("details") or {}).get("stage")
                        if stage_name:
                            stage_status[stage_name] = {
                                "STAGE_STARTED": "in_progress",
                                "STAGE_COMPLETED": "completed",
                                "STAGE_FAILED": "failed",
                            }[event["event_type"]]
                    if stage_status:
                        st.write("Stage status:")
                        for stage_name, status in sorted(stage_status.items()):
                            st.text(f"{stage_name}: {status}")
                    else:
                        st.text("Stages: pending")

if page in {"Batch detail", "Audit log"}:
    batches, error = api("GET", "/batches")
    show_error(error)
    batch_ids = [batch["id"] for batch in batches or []]

    if not batch_ids:
        st.info("No batches available.")
    else:
        batch_id = st.selectbox("Batch", batch_ids)

        if page == "Audit log":
            st.header("Audit log")
            audit, audit_error = api("GET", f"/batches/{batch_id}/audit")
            show_error(audit_error)
            if audit:
                st.dataframe(audit, use_container_width=True)

        else:
            batch, batch_error = api("GET", f"/batches/{batch_id}")
            show_error(batch_error)
            if batch:
                st.header(f"Batch {batch_id}")
                st.write("Status:", batch.get("status") or "unknown")
                st.write("Output hash:", batch.get("output_hash") or "not generated")

                col_run, col_resume = st.columns(2)
                with col_run:
                    if st.button("Run pending stages", type="primary"):
                        result, run_error = api("POST", f"/batches/{batch_id}/run")
                        show_error(run_error)
                        if result:
                            st.success("Run completed")
                            st.json(result)
                with col_resume:
                    if st.button("Resume"):
                        result, resume_error = api("POST", f"/batches/{batch_id}/resume")
                        show_error(resume_error)
                        if result:
                            st.success("Resume completed")
                            st.json(result)

            rejected, rejected_error = api("GET", f"/batches/{batch_id}/rejected-rows")
            show_error(rejected_error)
            st.subheader("Rejected rows")
            if rejected:
                st.dataframe(rejected, use_container_width=True)
            else:
                st.info("No rejected rows")

            alerts, alerts_error = api("GET", f"/batches/{batch_id}/alerts")
            show_error(alerts_error)
            st.subheader("Alerts")
            if alerts:
                grouped = defaultdict(list)
                for alert in alerts:
                    grouped[alert["severity"]].append(alert)
                for severity in ("warn", "info"):
                    if grouped[severity]:
                        st.markdown(f"### {severity.title()}")
                        st.dataframe(grouped[severity], use_container_width=True)
            else:
                st.info("No alerts")

            adjustments, adjustments_error = api("GET", f"/batches/{batch_id}/adjustments")
            show_error(adjustments_error)
            st.subheader("Adjustments")

            if adjustments:
                grouped_pairs = defaultdict(list)
                for adjustment in adjustments:
                    grouped_pairs[(adjustment["marker_id"], adjustment["subject"])].append(adjustment)

                reviewer = st.text_input("Reviewer name", key=f"reviewer-{batch_id}")

                pending_status = False
                for (marker_id, subject), rows in sorted(grouped_pairs.items(), key=lambda item: item[0]):
                    with st.expander(f"{subject} - {marker_id}", expanded=True):
                        for row in rows:
                            facts = row.get("facts") or {}
                            col1, col2 = st.columns(2)
                            with col1:
                                st.write("Student:", row["student_id"])
                                st.write("Raw score:", row["raw_score"])
                                st.write("Delta:", row["delta"])
                                st.write("Adjusted score:", row["adjusted_score"])
                            with col2:
                                st.write("Marker mean:", facts.get("marker_mean"))
                                st.write("Cohort mean:", facts.get("cohort_mean"))
                                st.write("Deviation:", facts.get("deviation"))
                                st.write("Status:", row["status"])

                        if rows and rows[0]["status"] == "PROPOSED":
                            pending_status = True
                            confirmed = st.checkbox(
                                f"Confirm decision for {subject} / {marker_id}",
                                key=f"confirm-{batch_id}-{marker_id}-{subject}",
                            )
                            decision_col1, decision_col2 = st.columns(2)
                            with decision_col1:
                                if st.button(
                                    "Approve",
                                    key=f"approve-{batch_id}-{marker_id}-{subject}",
                                    disabled=not confirmed,
                                ):
                                    if not reviewer.strip():
                                        st.error("Reviewer name is required")
                                    else:
                                        result, review_error = api("POST", f"/batches/{batch_id}/review", json={
                                            "marker_id": marker_id,
                                            "subject": subject,
                                            "decision": "APPROVED",
                                            "reviewer": reviewer.strip(),
                                        })
                                        show_error(review_error)
                                        if result:
                                            st.success("Approved")
                                            st.rerun()
                            with decision_col2:
                                if st.button(
                                    "Reject",
                                    key=f"reject-{batch_id}-{marker_id}-{subject}",
                                    disabled=not confirmed,
                                ):
                                    if not reviewer.strip():
                                        st.error("Reviewer name is required")
                                    else:
                                        result, review_error = api("POST", f"/batches/{batch_id}/review", json={
                                            "marker_id": marker_id,
                                            "subject": subject,
                                            "decision": "REJECTED",
                                            "reviewer": reviewer.strip(),
                                        })
                                        show_error(review_error)
                                        if result:
                                            st.success("Rejected")
                                            st.rerun()

                st.subheader("Export")
                export_disabled = pending_status
                if st.button("Create export", disabled=export_disabled, type="primary"):
                    export_result, export_error = api("POST", f"/batches/{batch_id}/export")
                    show_error(export_error)
                    if export_result:
                        st.success("Export is ready")
                        st.json(export_result)
                        csv_text, download_error = api("GET", f"/batches/{batch_id}/export/download")
                        show_error(download_error)
                        if csv_text:
                            st.download_button(
                                "Download CSV",
                                data=str(csv_text),
                                file_name=f"batch_{batch_id}.csv",
                                mime="text/csv",
                            )
            else:
                st.info("No proposed adjustments")
                st.subheader("Export")
                if st.button("Create export", type="primary"):
                    export_result, export_error = api("POST", f"/batches/{batch_id}/export")
                    show_error(export_error)
                    if export_result:
                        st.success("Export is ready")
                        st.json(export_result)
                        csv_text, download_error = api("GET", f"/batches/{batch_id}/export/download")
                        show_error(download_error)
                        if csv_text:
                            st.download_button(
                                "Download CSV",
                                data=str(csv_text),
                                file_name=f"batch_{batch_id}.csv",
                                mime="text/csv",
                            )
