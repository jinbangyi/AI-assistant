from hyperliquid.info import Info
from hyperliquid.utils import constants

info = Info(constants.MAINNET_API_URL, skip_ws=True)

def _test_get_user_state():
    address = "0x398a7bfa21e77a2579cbebc8bacf454af40b3553"

    def get_address_info(address: str):
        user_state = info.user_state(address)
        portfolio = info.portfolio(address)
        spot_user_state = info.spot_user_state(address)
        open_orders = info.open_orders(address)

        ret = {
            "user_state": user_state,
            "portfolio": portfolio,
            "spot_user_state": spot_user_state,
            "open_orders": open_orders,
        }
        return ret

    print(get_address_info(address))


# _test_get_user_state()

