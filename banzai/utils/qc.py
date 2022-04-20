from banzai import dbs


def save_qc_results(runtime_context, qc_results, image):
    """
    Save the Quality Control results to the logs table

    Parameters
    ----------
    runtime_context: object
                      Context instance with runtime values
    qc_results : dict
                 Dictionary of key value pairs to be saved to the logs table
    image : banzai.frames.ObservationFrame
            Image that should be linked

    Notes
    -----
    File name, site, instrument ID, dateobs, obstype, and timestamp are always saved in the database.
    """
    with dbs.get_session(db_address=runtime_context.DB_ADDRESS) as db_session:
        record = dbs.Log(filename=image.filename.replace('.fits', '').replace('.fz', ''),
                         site=image.site,
                         instrument_id=image.instrument.id,
                         dateobs=image.dateobs,
                         obstype=image.obstype,
                         results=qc_results)
        db_session.add(record)
    return record
