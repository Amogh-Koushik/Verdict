FROM gcc:14

RUN apt-get update \
    && apt-get install --yes --no-install-recommends time \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin runner \
    && mkdir /box \
    && chown runner:runner /box

USER runner
WORKDIR /box
