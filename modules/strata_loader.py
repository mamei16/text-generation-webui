import json
import threading
import time
from pathlib import Path
from typing import Any, List

from tqdm import tqdm

from modules import shared

from modules.logging_colors import logger
from modules.strata.strata_main import (StrataEngine, EngineStarting, EngineStuck, EngineDied, EngineSilent,
                                        EngineNonFinite, QueueWaitExpired, CTX_SLACK, ByteTokenizer, Detokenizer,
                                        request_stats, request_timings, REPEAT_STOP_TOKENS, prompt_tokens_seen)

THINK_END = "</think>"
EOS_TOK = "<|im_end|>"
strata_valid_cache_types = {"int8", "q4_0"}


class StrataLoader:

    def __init__(self, model_path):
        """
        Initialize and start the Strata engine.
        """
        self.model_path = model_path
        self.vocabulary_size = None
        self.n_ctx = None
        self.bos_token = "<s>"
        self.last_prompt_token_count = 0
        self.tokens_predicted = 0
        self.tokens_evaluated = 0
        self.engine = None
        self.status = {"busy": False, "queued": 0}
        self.max_repeat_stok_tokens = REPEAT_STOP_TOKENS
        self.tok = None
        self.think_end_tok_id = None
        self.eos_tok_id = None

        # Start the engine
        self._start_engine()

    def encode(self, text, add_bos_token=False, **kwargs):
        if add_bos_token:
            logger.warning("Strata encode: add_bos_token is True but will be ignored")
        return self.tok.encode(text, parse_special=True)

    def decode(self, token_ids, **kwargs):
        return self.tok.decode(token_ids)

    def prepare_sampling_params(self, state):
        sampling_params = {
            "temperature": state["temperature"] if not state["dynamic_temperature"] else (state["dynatemp_low"] + state["dynatemp_high"]) / 2,
            "top_k": state["top_k"],
            "top_p": state["top_p"],
            "min_p": state["min_p"],
            "repeat_penalty": state["repetition_penalty"],
            "repeat_last_n": state["repetition_penalty_range"],
            "penalty_last_n": state["repetition_penalty_range"],
            "presence_penalty": state["presence_penalty"],
            "frequency_penalty": state["frequency_penalty"],
            "seed": state["seed"],
            "return_progress": True
        }

        logit_bias = []
        if state['custom_token_bans']:
            logit_bias.extend([[int(token_id.strip()), False] for token_id in state['custom_token_bans'].split(',') if token_id.strip()])

        if state.get('logit_bias'):
            for token_id_str, bias in state['logit_bias'].items():
                logit_bias.append([int(token_id_str), bias])

        if logit_bias:
            sampling_params["logit_bias"] = logit_bias

        n_probs = state.get('logprobs', 0)
        if n_probs and n_probs > 0:
            sampling_params["logprobs"] = True
            sampling_params["top_logprobs"] = n_probs

        return sampling_params

    def _process_images_for_generation(self, state: dict) -> List[Any]:
        """
        Process all possible image inputs and return PIL images
        """
        raise NotImplementedError

    def is_multimodal(self) -> bool:
        """Check if this model supports multimodal input."""
        return shared.args.mmproj not in [None, 'None']


    def max_tokens(self, prompt_ids):
        ctx = self.engine.max_context

        if ctx <= 0:
            ctx = getattr(self.engine, "known_ctx", 0)
        if ctx <= 0:
            raise EngineStarting("the engine is starting (a minute or two); try again shortly")

        prompt_len = len(prompt_ids)
        room = ctx - CTX_SLACK - prompt_len
        if room < 1:
            logger.error("The prompt exceeds the available context size, try increasing it")
        return room

    def generate_with_streaming(self, prompt, state):
        sampling = self.prepare_sampling_params(state)
        ids = self.encode(prompt)
        prompt_len = len(ids)

        max_new_tokens = self.max_tokens(ids)

        detok, n, finish = Detokenizer(self.tok), 0, "length"
        run_tok, run_len, repeated = None, 0, False  # #606: the current run of one repeated token

        raw_ids = []  # every generated id (STRATA_DEBUG: dump raw model text)
        timed_out = False
        first_engine_token_s = None  # this request, from entering the engine queue; includes queue/prefill
        cancel = threading.Event()

        self.status.update(busy=True, phase="reading the prompt", prompt_tokens=len(ids),
                           generated=0, started=time.time(), first_token=None, tool=None, tail="",
                           max_tokens=max_new_tokens, reasoning_recoveries=0)
        last_print = time.monotonic()
        start_time = time.monotonic()
        segments = []
        full_text = ""
        last_n = 0
        last_n_processed = 0
        stop_event = state.get('stop_event')
        before = getattr(self.engine, "last", None)
        self.tokens_predicted = 0
        self.tokens_evaluated = prompt_len

        try:
            segment_before = getattr(self.engine, "last", None)
            seg, wrap, leaving = [], False, False  # this pass's tokens; the budget is reached; closed
            for _try in range(2):
                # If model stopping generation early during reasoning. Close reasoning block and continue generation.
                if _try == 1:
                    full_text += THINK_END
                    finished_reasoning = True
                    max_new_tokens = self.max_tokens(ids)
                else:
                    finished_reasoning = False
                    thinking_n = 0
                gen = self.engine.generate(ids, max_new_tokens, sampling, cancel)
                try:
                    for t in gen:
                        if shared.stop_everything or (stop_event and stop_event.is_set()):
                            break
                        if t is None:  # heartbeat while the engine is quiet
                            if first_engine_token_s is None:
                                pr = getattr(self.engine, "progress",
                                            None)  # (position reached, prompt tokens): a reused prefix counts
                                if pr and (n_processed_tokens := pr[0]) > last_n_processed:
                                    last_n_processed = n_processed_tokens
                                    n_total_tokens = pr[1]
                                    percent_processed = int((n_processed_tokens / n_total_tokens) * 100)
                                    rate = n_processed_tokens / (time.monotonic() - start_time)
                                    remaining = (n_total_tokens - n_processed_tokens) / rate if rate else 0
                                    remaining_str = tqdm.format_interval(remaining) if rate else '?'
                                    yield full_text + f'​*Processing... (__{percent_processed}%__ ETA: {remaining_str})*'
                            continue
                        n += 1
                        self.tokens_predicted = n
                        self.last_prompt_token_count = n
                        if first_engine_token_s is None:
                            first_engine_token_s = time.monotonic()
                        if t == self.think_end_tok_id:
                            finished_reasoning = True
                        if not finished_reasoning:
                            thinking_n = n
                            # Model sends eos token before finished thinking. Don't let EOS reach high level generation
                            # func to avoid it breaking out of loop and closing this generator function early. This
                            # early stopping issue is handled below
                            if state['enable_thinking'] and t == self.eos_tok_id:
                                break
                        raw_ids.append(t)
                        seg.append(t)
                        run_len = run_len + 1 if t == run_tok else 1
                        run_tok = t
                        if self.max_repeat_stok_tokens and run_len >= self.max_repeat_stok_tokens:
                            repeated = True  # #606: a degenerate output, not an answer: end it here
                            break
                        full_text += detok.push(t)
                        yield full_text
                        now = time.monotonic()
                        if now - last_print > 3:
                            rate = (n - last_n) / (now - last_print)
                            el = now - start_time
                            logger.info(f"[strata]: {n} of {max_new_tokens} tokens, {rate:.1f} tok/s, {el:.0f} s")
                            last_n = n
                            last_print = now

                    if repeated:
                        logger.warning(f"[strata] the reply repeated one token ({self.tok.decode([run_tok])!r}) "
                                       f"{run_len} times in a row: ended as \"length\" (repeat_stop_tokens in "
                                       "strata-<model>.json; 0 turns this off). If a new request with a short prompt does the "
                                       "same, restart the server and report it (#606)")
                except EngineNonFinite as e:
                    logger.error(str(e))
                except EngineDied:
                    raise
                except QueueWaitExpired:
                    timed_out = True
                    raise
                except ValueError as e:  # the engine's ERR line (it may have ended after it)
                    logger.error(f"[strata] the engine reported an error: {e}")
                    raise
                except GeneratorExit:  # the client went away: an engine that never acknowledges
                    leaving = True     # the STOP below is ended, but no error replaces this
                    raise
                finally:
                    try:
                        gen.close()  # STOP+drain to THIS request's DONE while still holding the
                        #                         fifo, so a stop-token break can't leave the shared engine
                        #                         queue mid-drain for the next request to read as its own DONE
                    except EngineSilent as e:  # #481: the STOP was never acknowledged: the engine is ended
                        note = self.engine.death_note() if hasattr(self.engine, "death_note") else ""
                        logger.error("Strata Engine died. " + note)
                        if not leaving and not cancel.is_set():
                            raise
                    segment_done = getattr(self.engine, "last", None)
                    if segment_done is not None and segment_done is not segment_before:
                        segments.append(dict(segment_done))

                # Handle case where the model stopped generating in the middle of reasoning and never started its
                # actual answer
                if (state['enable_thinking'] and not finished_reasoning and n < max_new_tokens and n - thinking_n == 0
                    and not request_stats(segments).get("finish") == "cancel"):
                    ids.extend(raw_ids)
                    ids.append(self.think_end_tok_id)
                    max_new_tokens -= 1
                    logger.warning(f"[strata] Model stopped early during reasoning. Continuing output.")
                else:
                    break

        except OSError as e:
            logger.error("OSError: " + str(e))
        except (EngineDied, EngineStuck) as e:  # mid-stream: say so, then end the stream properly
            logger.error(str(e))
        except ValueError as e:  # the engine's ERR after the stream started
            logger.error(str(e))
        finally:
            # #266: settle this request's status, history and totals while still holding the fifo: once
            # it is released the next request sets its own status, which this must not record or clear
            if self.status.get("busy") and not timed_out:
                last = request_stats(segments)  # only this request's own DONEs count
                if last.get("finish") == "length":
                    logger.warning("[strata] Generation stopped early, max. context reached")
                    self.tokens_predicted = max_new_tokens + CTX_SLACK
                hit_rate = round(last["hits"] / last["lookups"], 3) if last.get("lookups") else None
                # #588: the share the GPU read over PCIe (--pcie-frac) or another GPU computed - not in
                # the hit rate's lookups, which is the VRAM share of the rest (None: an older engine)
                routed = (last.get("lookups") or 0) + (last.get("offloaded") or 0)
                pcie_share = round(last["offloaded"] / routed, 3) \
                    if last.get("offloaded") is not None and routed else None
                seen = prompt_tokens_seen(len(ids), last)  # #471: < len(ids) when cancelled mid-read
                fresh = getattr(self.engine, "last", None)
                timings = None
                if fresh is not None and fresh is not before:  # the engine's clock for THIS request
                    timings = request_timings(seen, n, last)
                    self.last_timings = dict(timings, at=int(time.time())) if timings else None

                self.last_request_at = time.time()
                prefill_time = None
                num_decode_tokens = None
                num_drafted_tokens = None
                if timings:
                    num_prefill_tokens = timings["prompt_n"]
                    prefill_time = int(timings["prompt_ms"] / 1000)
                    prefill_rate = timings["prompt_per_second"]
                    num_decode_tokens = timings["predicted_n"]
                    decode_time = int(timings["predicted_ms"] / 1000)
                    decode_rate = timings["predicted_per_second"]
                    num_drafted_tokens = timings["draft_n"]
                    num_accepted_draft_tokens = timings["draft_n_accepted"]
                else:
                    now = time.monotonic()
                    num_prefill_tokens = len(ids)

                    if first_engine_token_s:
                        prefill_time = first_engine_token_s - start_time
                        prefill_rate = num_prefill_tokens / prefill_time
                        num_decode_tokens = n
                        decode_time = now - first_engine_token_s
                        ft = first_engine_token_s
                        decode_rate = n / max(1e-6, now - ft) if ft else 0.0

                stats_string = f"[strata] done. Stats:\n"
                if prefill_time is not None:
                    stats_string += f"[strata] Prefill: {num_prefill_tokens} tokens in {int(prefill_time)} s ({prefill_rate:.1f} tok/s)\n"
                if num_decode_tokens is not None:
                    stats_string += f"[strata] Decode: {num_decode_tokens} tokens in {int(decode_time)} s ({decode_rate:.1f} tok/s)\n"
                if num_drafted_tokens is not None:
                    stats_string += f"[strata] Draft_n: {num_drafted_tokens}, {num_accepted_draft_tokens} accepted"
                hit_msg = f"\n[strata] Expert cache {hit_rate * 100:.1f}% hit" if hit_rate is not None else ""
                if hit_msg and pcie_share:
                    hit_msg += f" (+{pcie_share * 100:.1f}% of the routed experts over PCIe)"
                    stats_string += hit_msg
                logger.info(stats_string + "\n")
            self.status["busy"] = False
            self.status.pop("tail", None)  # #212: the answer's end is not kept once it is done

    def generate(self, prompt, state):
        output = ""
        for output in self.generate_with_streaming(prompt, state):
            pass

        return output

    def get_logits(self, prompt, state, n_probs=128):
        raise NotImplementedError()

    def _start_engine(self):
        """Start the Strata engine and wait until it's ready."""
        with open(self.model_path, "r") as f:
            cfg = json.load(f)

        self.tok = ByteTokenizer()
        tpath = Path(cfg.get("tokenizer"))
        if not (tpath / "vocab.json").exists():
            raise ValueError(f"the strata model's tokenizer is missing ({tpath / 'vocab.json'}); run setup again")
        if (tpath / "vocab.json").exists():
            import modules.strata.strata_tokenizer as ST
            vocab = json.loads((tpath / "vocab.json").read_text(encoding="utf-8"))
            tokens = [None] * len(vocab)
            for t, i in vocab.items():
                tokens[i] = t
            merges = (tpath / "merges.txt").read_text(encoding="utf-8").split("\n")
            types = json.loads((tpath / "token_type.json").read_text())
            self.tok = ST.Tokenizer(tokens, merges, types)

        self.think_end_tok_id = self.encode(THINK_END)[0]
        self.eos_tok_id = self.encode(EOS_TOK)[0]

        args = cfg["args"]
        if "--max-context" in args:
            target_index = args.index("--max-context")
            if shared.args.ctx_size > 0:
                args[target_index+1] = str(shared.args.ctx_size)
                self.n_ctx = shared.args.ctx_size
            else:
                self.n_ctx = int(args[target_index+1])

        if shared.args.cache_type in strata_valid_cache_types:
            if "--kv" in args:
                target_index = args.index("--kv")
                args[target_index + 1] = str(shared.args.cache_type)
            else:
                args.extend(["--kv", str(shared.args.cache_type)])

        if shared.args.mmap_experts and "--mmap-experts" not in args:
            args.append("--mmap-experts")

        self.engine = StrataEngine(cfg["exe"], cfg["args"], cwd=cfg.get("cwd"), log=cfg.get("log"), env=cfg.get("env"))

    def __enter__(self):
        """Support for context manager."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Support for context manager."""
        self.stop()

    def __del__(self):
        """Cleanup when the object is deleted."""
        self.stop()

    def stop(self):
        """Stop the engine process."""
        if self.engine:
            self.engine.close()
            self.engine = None
